"""Load GPS activities from GPX, TCX and FIT files (optionally gzipped).

Also understands the layout of a Strava bulk export: if a directory contains
an ``activities.csv`` its activity types, names and dates are attached to the
matching files, since Strava's own GPX files usually omit the activity type.
"""

from __future__ import annotations

import csv
import gzip
import io
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

from .geo import segment_lengths

SUFFIXES = (".gpx", ".tcx", ".fit")

# Strava GPX files encode the sport as a number; 9 is running.
_STRAVA_TYPE_CODES = {"1": "ride", "4": "hike", "9": "run", "10": "walk", "16": "swim"}


@dataclass
class Activity:
    """One recorded activity: a list of GPS segments plus metadata."""

    path: str
    segments: list[np.ndarray]  # each an (N, 2) array of [lat, lon]
    name: str | None = None
    sport: str | None = None
    start_time: datetime | None = None
    extra: dict = field(default_factory=dict)

    @property
    def num_points(self) -> int:
        return sum(len(s) for s in self.segments)

    @property
    def distance_m(self) -> float:
        return float(sum(segment_lengths(s).sum() for s in self.segments))

    def representative_point(self) -> np.ndarray:
        """Median point, a robust stand-in for "where this activity was"."""
        return np.median(np.concatenate(self.segments), axis=0)


def normalize_sport(value: str | None) -> str | None:
    """Map the many spellings of a sport onto a short lowercase name."""
    if value is None:
        return None
    v = value.strip().lower()
    if not v:
        return None
    if v in _STRAVA_TYPE_CODES:
        return _STRAVA_TYPE_CODES[v]
    v = v.replace("_", " ")
    if "run" in v or v == "jog":
        return "run"
    if "ride" in v or "cycl" in v or "bik" in v:
        return "ride"
    if "walk" in v:
        return "walk"
    if "hik" in v:
        return "hike"
    if "swim" in v:
        return "swim"
    return v


# --------------------------------------------------------------------------
# File discovery


def _strip_gz(name: str) -> str:
    return name[:-3] if name.lower().endswith(".gz") else name


def is_activity_file(path: Path) -> bool:
    return _strip_gz(path.name).lower().endswith(SUFFIXES)


def find_activity_files(inputs: Iterable[str | os.PathLike]) -> list[Path]:
    """Expand files and directories (searched recursively) into activity files."""
    found: list[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            found.extend(sorted(f for f in p.rglob("*") if f.is_file() and is_activity_file(f)))
        elif p.is_file():
            found.append(p)
        else:
            raise FileNotFoundError(f"No such file or directory: {p}")
    return found


def load_strava_index(inputs: Iterable[str | os.PathLike]) -> dict[str, dict]:
    """Read ``activities.csv`` from any input directory of a Strava export.

    Returns a mapping from activity file base name (without ``.gz``) to a dict
    with ``sport``, ``name`` and ``start_time``.
    """
    index: dict[str, dict] = {}
    for item in inputs:
        p = Path(item)
        if not p.is_dir():
            continue
        for csv_path in p.rglob("activities.csv"):
            with open(csv_path, newline="", encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    filename = (row.get("Filename") or "").strip()
                    if not filename:
                        continue
                    key = _strip_gz(Path(filename).name)
                    index[key] = {
                        "sport": normalize_sport(row.get("Activity Type")),
                        "name": (row.get("Activity Name") or "").strip() or None,
                        "start_time": _parse_strava_date(row.get("Activity Date")),
                    }
    return index


def _parse_strava_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%b %d, %Y, %I:%M:%S %p", "%d %b %Y, %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Parsing


def _read_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if path.name.lower().endswith(".gz") or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


def load_activity(path: str | os.PathLike, strava_index: dict | None = None) -> Activity:
    """Parse one activity file. Raises ``ValueError`` if it cannot be read."""
    path = Path(path)
    kind = Path(_strip_gz(path.name)).suffix.lower()
    data = _read_bytes(path)
    if kind == ".gpx":
        act = parse_gpx(data, str(path))
    elif kind == ".tcx":
        act = parse_tcx(data, str(path))
    elif kind == ".fit":
        act = parse_fit(data, str(path))
    else:
        raise ValueError(f"Unsupported file type: {path.name}")

    meta = (strava_index or {}).get(_strip_gz(path.name))
    if meta:
        act = replace(
            act,
            sport=meta["sport"] or act.sport,
            name=meta["name"] or act.name,
            start_time=act.start_time or meta["start_time"],
        )
    return act


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _xml_root(data: bytes) -> ET.Element:
    # Some exporters prepend whitespace or a BOM before the XML declaration.
    data = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    try:
        return ET.parse(io.BytesIO(data)).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc


def parse_gpx(data: bytes, path: str = "<gpx>") -> Activity:
    root = _xml_root(data)
    segments: list[np.ndarray] = []
    name = sport = None
    start = None

    for el in root.iter():
        tag = _local(el.tag)
        if tag in ("trk", "rte"):
            for child in el:
                ctag = _local(child.tag)
                if ctag == "name" and name is None and child.text:
                    name = child.text.strip()
                elif ctag == "type" and sport is None and child.text:
                    sport = normalize_sport(child.text)
        if tag in ("trkseg", "rte"):
            pts = []
            for pt in el:
                if _local(pt.tag) not in ("trkpt", "rtept"):
                    continue
                try:
                    pts.append((float(pt.get("lat")), float(pt.get("lon"))))
                except (TypeError, ValueError):
                    continue
                if start is None:
                    for child in pt:
                        if _local(child.tag) == "time":
                            start = _parse_time(child.text)
                            break
            if pts:
                segments.append(np.array(pts, dtype=np.float64))

    if start is None:
        for el in root.iter():
            if _local(el.tag) == "time":
                start = _parse_time(el.text)
                break
    return Activity(path, segments, name=name, sport=sport, start_time=start)


def parse_tcx(data: bytes, path: str = "<tcx>") -> Activity:
    root = _xml_root(data)
    segments: list[np.ndarray] = []
    sport = start = None

    for el in root.iter():
        tag = _local(el.tag)
        if tag == "Activity" and sport is None:
            sport = normalize_sport(el.get("Sport"))
        elif tag == "Id" and start is None:
            start = _parse_time(el.text)
        elif tag == "Track":
            pts = []
            for tp in el:
                if _local(tp.tag) != "Trackpoint":
                    continue
                lat = lon = None
                for child in tp:
                    if _local(child.tag) == "Position":
                        for c in child:
                            if _local(c.tag) == "LatitudeDegrees":
                                lat = float(c.text)
                            elif _local(c.tag) == "LongitudeDegrees":
                                lon = float(c.text)
                    elif _local(child.tag) == "Time" and start is None:
                        start = _parse_time(child.text)
                if lat is not None and lon is not None:
                    pts.append((lat, lon))
            if pts:
                segments.append(np.array(pts, dtype=np.float64))
    return Activity(path, segments, sport=sport, start_time=start)


_SEMICIRCLE = 180.0 / 2**31


def parse_fit(data: bytes, path: str = "<fit>") -> Activity:
    try:
        import fitdecode
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ValueError("Reading .fit files needs the 'fitdecode' package (pip install fitdecode)") from exc

    pts = []
    sport = start = None
    try:
        with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.DISABLED) as reader:
            for frame in reader:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name == "record":
                    lat = _fit_field(frame, "position_lat")
                    lon = _fit_field(frame, "position_long")
                    if start is None:
                        ts = _fit_field(frame, "timestamp")
                        if isinstance(ts, datetime):
                            start = ts
                    if lat is None or lon is None:
                        continue
                    pts.append((lat * _SEMICIRCLE, lon * _SEMICIRCLE))
                elif frame.name in ("sport", "session") and sport is None:
                    value = _fit_field(frame, "sport")
                    if value is not None:
                        sport = normalize_sport(str(value))
    except fitdecode.FitError as exc:
        raise ValueError(f"Invalid FIT file: {exc}") from exc

    segments = [np.array(pts, dtype=np.float64)] if pts else []
    return Activity(path, segments, sport=sport, start_time=start)


def _fit_field(frame, name):
    if frame.has_field(name):
        return frame.get_value(name)
    return None


# --------------------------------------------------------------------------
# Cleaning


def clean_activity(act: Activity, max_gap_m: float = 250.0, trim_ends_m: float = 0.0) -> Activity:
    """Tidy up raw GPS data.

    * drops points at (0, 0) and non-finite values (common GPS placeholders),
    * splits a segment wherever two consecutive points are more than
      ``max_gap_m`` apart, so signal loss or a paused-then-moved watch doesn't
      draw a straight line across the map,
    * optionally removes the first and last ``trim_ends_m`` metres of the whole
      activity, which hides where you start and finish (usually home) when the
      poster goes on the wall or online.
    """
    segments = []
    for seg in act.segments:
        ok = np.isfinite(seg).all(axis=1) & ~((seg[:, 0] == 0) & (seg[:, 1] == 0))
        ok &= (np.abs(seg[:, 0]) <= 90) & (np.abs(seg[:, 1]) <= 180)
        seg = seg[ok]
        if len(seg) < 2:
            continue
        if max_gap_m and max_gap_m > 0:
            gaps = np.nonzero(segment_lengths(seg) > max_gap_m)[0]
            segments.extend(p for p in np.split(seg, gaps + 1) if len(p) >= 2)
        else:
            segments.append(seg)

    if trim_ends_m > 0 and segments:
        segments = _trim(segments, trim_ends_m)
        segments = _trim([s[::-1] for s in reversed(segments)], trim_ends_m)
        segments = [s[::-1] for s in reversed(segments)]

    return replace(act, segments=segments)


def _trim(segments: list[np.ndarray], metres: float) -> list[np.ndarray]:
    """Remove the first ``metres`` of travel from a list of segments."""
    out: list[np.ndarray] = []
    remaining = metres
    for seg in segments:
        if remaining <= 0:
            out.append(seg)
            continue
        cum = np.concatenate([[0.0], np.cumsum(segment_lengths(seg))])
        if cum[-1] <= remaining:
            remaining -= cum[-1]
            continue
        idx = int(np.searchsorted(cum, remaining))
        remaining = 0
        if len(seg) - idx >= 2:
            out.append(seg[idx:])
    return out


def iter_activities(paths: list[Path], strava_index: dict | None = None,
                    jobs: int = 1) -> Iterator[tuple[Path, Activity | Exception]]:
    """Load many files, yielding ``(path, activity_or_error)``."""
    if jobs == 1 or len(paths) < 32:
        for p in paths:
            try:
                yield p, load_activity(p, strava_index)
            except Exception as exc:  # noqa: BLE001 - reported to the user
                yield p, exc
        return

    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=jobs if jobs > 0 else None) as pool:
        for p, result in zip(paths, pool.map(_load_safe, paths, [strava_index] * len(paths), chunksize=16)):
            yield p, result


def _load_safe(path: Path, strava_index: dict | None):
    try:
        return load_activity(path, strava_index)
    except Exception as exc:  # noqa: BLE001
        return exc
