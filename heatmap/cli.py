"""Command-line interface: ``run-heatmap EXPORT_DIR -o heatmap.png``."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from . import __version__
from .basemap import PROVIDERS, fetch_basemap, is_light, resolve_provider
from .geo import BBox, bbox_of, densest_cluster, from_world, to_world
from .parsers import clean_activity, find_activity_files, iter_activities, load_strava_index
from .poster import PAPER_SIZES_MM, compose, layout, paper_pixels
from .render import DEFAULT_BACKGROUNDS, PALETTES, Viewport, render
from .web import build_html


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run-heatmap",
        description="Draw every run you've done as a glowing heatmap. Accepts GPX, TCX and FIT "
                    "files (gzipped too), folders of them, or an unzipped Strava bulk export.",
    )
    p.add_argument("inputs", nargs="+", help="activity files and/or directories (searched recursively)")
    p.add_argument("-o", "--output", default="heatmap.png", help="output image (default: %(default)s)")
    p.add_argument("--html", metavar="FILE", help="also write an interactive, zoomable map to FILE")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    f = p.add_argument_group("which activities")
    f.add_argument("--sport", default="run",
                   help="keep only this sport (run, ride, walk, hike, ...) or 'all' (default: %(default)s). "
                        "Activities with no recorded sport are kept unless --strict-sport is given")
    f.add_argument("--strict-sport", action="store_true", help="drop activities whose sport is unknown")
    f.add_argument("--since", type=_date, help="only activities on/after YYYY-MM-DD")
    f.add_argument("--until", type=_date, help="only activities on/before YYYY-MM-DD")
    f.add_argument("--max-gap", type=float, default=250, metavar="M",
                   help="break a line where GPS points jump more than M metres (default: %(default)s)")
    f.add_argument("--trim-ends", type=float, default=0, metavar="M",
                   help="privacy: hide the first and last M metres of every activity")
    f.add_argument("-j", "--jobs", type=int, default=0,
                   help="parallel file parsing processes (default: all CPUs; 1 disables)")

    r = p.add_argument_group("where")
    r.add_argument("--region", default="auto",
                   help="'auto' (densest cluster of activities, default), 'all', or "
                        "SOUTH,WEST,NORTH,EAST in degrees")
    r.add_argument("--center", metavar="LAT,LON", help="centre the map on a point (use with --radius)")
    r.add_argument("--radius", type=float, default=10, metavar="KM",
                   help="half-width of the map around --center (default: %(default)s km)")
    r.add_argument("--padding", type=float, default=0.04,
                   help="extra space around the routes as a fraction (default: %(default)s)")

    s = p.add_argument_group("size")
    s.add_argument("--width", type=int, default=3000, help="image width in pixels (default: %(default)s)")
    s.add_argument("--height", type=int, help="image height in pixels (default: fit the region)")
    s.add_argument("--poster", metavar="SIZE",
                   help=f"lay out a print-ready poster: {', '.join(PAPER_SIZES_MM)}, or e.g. 50x70cm")
    s.add_argument("--dpi", type=int, default=300, help="poster resolution (default: %(default)s)")
    s.add_argument("--landscape", action="store_true", help="landscape poster")
    s.add_argument("--full-bleed", action="store_true", help="poster map runs to the paper edge")

    look = p.add_argument_group("look")
    look.add_argument("--palette", default="fire", choices=sorted(PALETTES), help="colour ramp (default: %(default)s)")
    look.add_argument("--background", help="background colour, e.g. '#000' (default depends on palette)")
    look.add_argument("--line-width", type=float, help="line width in pixels (default: scales with size)")
    look.add_argument("--glow", type=float, default=1.0, help="halo strength, 0 for crisp lines (default: %(default)s)")
    look.add_argument("--scale", default="log", choices=["log", "linear", "equalize"],
                      help="how run counts map to brightness (default: %(default)s)")
    look.add_argument("--basemap", default="none",
                      help=f"draw streets underneath: none, {', '.join(PROVIDERS)}, or a tile URL "
                           "template like https://.../{z}/{x}/{y}.png (downloads tiles; default: none)")
    look.add_argument("--basemap-dim", type=float, default=0.55, help="basemap brightness (default: %(default)s)")
    look.add_argument("--title", help="poster title (default: none for images, 'Every Run' for posters)")
    look.add_argument("--subtitle", help="poster subtitle (default: stats line; '' to hide)")
    look.add_argument("--font", help="path to a .ttf/.otf font for the poster caption")
    return p


def _date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def select_region(args, activities) -> BBox:
    if args.center:
        lat, lon = (float(v) for v in args.center.split(","))
        return BBox.around(lat, lon, args.radius)
    world = [np.stack(to_world(s[:, 0], s[:, 1]), axis=1) for a in activities for s in a.segments]
    if args.region == "all":
        return bbox_of(world).pad(args.padding)
    if args.region == "auto":
        centers = np.array([a.representative_point() for a in activities])
        chosen = densest_cluster(centers)
        pts = [np.stack(to_world(s[:, 0], s[:, 1]), axis=1)
               for i in chosen for s in activities[i].segments]
        return bbox_of(pts).pad(args.padding)
    try:
        south, west, north, east = (float(v) for v in args.region.split(","))
    except ValueError:
        raise SystemExit(f"--region must be auto, all or SOUTH,WEST,NORTH,EAST (got {args.region!r})")
    return BBox.from_latlon(south, west, north, east)


def stats_line(activities, sport: str) -> str:
    km = sum(a.distance_m for a in activities) / 1000
    noun = {"run": "runs", "ride": "rides", "walk": "walks", "hike": "hikes", "swim": "swims"}.get(sport, "activities")
    if len(activities) == 1:
        noun = noun[:-1] if noun != "activities" else "activity"
    parts = [f"{len(activities):,} {noun}", f"{km:,.0f} km"]
    years = sorted({a.start_time.year for a in activities if a.start_time})
    if years:
        parts.append(str(years[0]) if years[0] == years[-1] else f"{years[0]} – {years[-1]}")
    return "  ·  ".join(parts)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        files = find_activity_files(args.inputs)
    except FileNotFoundError as exc:
        _log(str(exc))
        return 2
    if not files:
        _log("No .gpx, .tcx or .fit files found.")
        return 1
    strava = load_strava_index(args.inputs)
    _log(f"Reading {len(files):,} files" + (" (with Strava activities.csv)" if strava else "") + " ...")

    sport = args.sport.lower()
    activities, failed, skipped = [], 0, 0
    for path, result in iter_activities(files, strava, jobs=args.jobs):
        if isinstance(result, Exception):
            failed += 1
            _log(f"  skipped {path}: {result}")
            continue
        act = result
        if sport != "all":
            unknown_ok = act.sport is None and not args.strict_sport
            if act.sport != sport and not unknown_ok:
                skipped += 1
                continue
        if act.start_time and (args.since or args.until):
            d = act.start_time.astimezone(timezone.utc).date()
            if (args.since and d < args.since) or (args.until and d > args.until):
                skipped += 1
                continue
        act = clean_activity(act, max_gap_m=args.max_gap, trim_ends_m=args.trim_ends)
        if act.segments:
            activities.append(act)
        else:
            skipped += 1

    msg = f"Using {len(activities):,} activities"
    if skipped:
        msg += f", skipped {skipped:,} (other sports, dates or no GPS)"
    if failed:
        msg += f", {failed:,} unreadable"
    _log(msg)
    if not activities:
        _log("Nothing to draw. Try --sport all, or check the files contain GPS tracks.")
        return 1

    bbox = select_region(args, activities)
    subtitle = stats_line(activities, sport) if args.subtitle is None else args.subtitle
    palette = args.palette
    background = args.background or DEFAULT_BACKGROUNDS.get(palette, "#050508")

    if args.poster:
        page = paper_pixels(args.poster, args.dpi, args.landscape)
        title = "Every Run" if args.title is None else args.title
        lay = layout(page, with_caption=bool(title or subtitle), full_bleed=args.full_bleed)
        width, height = lay.map_size
    else:
        width = args.width
        aspect = bbox.width / bbox.height if bbox.height > 0 else 1.0
        height = args.height or max(1, int(round(width / aspect)))
    bbox = bbox.fit_aspect(width / height)
    _log(f"Rendering {width:,} x {height:,} px ...")

    base = attribution = None
    if args.basemap != "none":
        try:
            _, attribution = resolve_provider(args.basemap)
            base = fetch_basemap(bbox, width, height, args.basemap, dim=args.basemap_dim)
        except Exception as exc:  # noqa: BLE001 - a missing basemap shouldn't sink the render
            _log(f"  basemap unavailable ({exc}); drawing without it")
            attribution = None
        if base is not None and args.background is None and is_light(base) and palette in ("fire", "ice", "neon", "strava", "mono"):
            _log("  tip: light basemaps look best with --palette ink or risograph")

    img = render(activities, Viewport(bbox, width, height), palette=palette, background=background,
                 line_width=args.line_width, glow=args.glow, scale=args.scale, base=base)

    if args.poster:
        img = compose(img, lay, background=background, title=title, subtitle=subtitle,
                      attribution=attribution, font=args.font)
        dpi = (args.dpi, args.dpi)
    else:
        if attribution:
            img = compose(img, layout(img.size, with_caption=False, full_bleed=True),
                          background=background, title=None, subtitle=None, attribution=attribution)
        dpi = None

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    save_kwargs = {"dpi": dpi} if dpi else {}
    if out.suffix.lower() in (".jpg", ".jpeg"):
        save_kwargs["quality"] = 95
    img.save(out, **save_kwargs)
    _log(f"Wrote {out}  ({subtitle})" if subtitle else f"Wrote {out}")

    if args.html:
        lat0, lon0 = from_world(bbox.x0, bbox.y1)
        lat1, lon1 = from_world(bbox.x1, bbox.y0)
        html = build_html(activities, title=args.title or "Every Run", subtitle=subtitle,
                          bounds_latlon=[[float(lat0), float(lon0)], [float(lat1), float(lon1)]])
        Path(args.html).write_text(html, encoding="utf-8")
        _log(f"Wrote {args.html}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
