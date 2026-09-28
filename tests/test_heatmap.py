import gzip
import io
import textwrap

import numpy as np
import pytest
from PIL import Image

from heatmap import cli
from heatmap.basemap import choose_zoom, fetch_basemap
from heatmap.geo import BBox, densest_cluster, from_world, segment_lengths, to_world
from heatmap.parsers import (Activity, clean_activity, find_activity_files, load_activity,
                             load_strava_index, normalize_sport, parse_gpx, parse_tcx)
from heatmap.poster import layout, paper_pixels
from heatmap.render import Viewport, accumulate, gaussian_blur, render, tone_map
from heatmap.web import build_html

GPX = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
      <trk><name>Morning Run</name><type>9</type>
        <trkseg>
          <trkpt lat="51.5000" lon="-0.1000"><time>2021-05-01T07:00:00Z</time></trkpt>
          <trkpt lat="51.5010" lon="-0.1000"><time>2021-05-01T07:00:30Z</time></trkpt>
          <trkpt lat="51.5020" lon="-0.1000"><time>2021-05-01T07:01:00Z</time></trkpt>
        </trkseg>
        <trkseg>
          <trkpt lat="51.5020" lon="-0.0990"/>
          <trkpt lat="51.5020" lon="-0.0980"/>
        </trkseg>
      </trk>
    </gpx>
""")

TCX = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
      <Activities><Activity Sport="Running"><Id>2020-01-02T08:00:00Z</Id>
        <Lap><Track>
          <Trackpoint><Time>2020-01-02T08:00:00Z</Time>
            <Position><LatitudeDegrees>40.0</LatitudeDegrees><LongitudeDegrees>-73.0</LongitudeDegrees></Position>
          </Trackpoint>
          <Trackpoint><Time>2020-01-02T08:00:05Z</Time></Trackpoint>
          <Trackpoint><Time>2020-01-02T08:00:10Z</Time>
            <Position><LatitudeDegrees>40.001</LatitudeDegrees><LongitudeDegrees>-73.0</LongitudeDegrees></Position>
          </Trackpoint>
        </Track></Lap>
      </Activity></Activities>
    </TrainingCenterDatabase>
""")


def line(lat0, lon0, lat1, lon1, n=50):
    return np.stack([np.linspace(lat0, lat1, n), np.linspace(lon0, lon1, n)], axis=1)


# --- geo -------------------------------------------------------------------

def test_projection_round_trip():
    lat, lon = np.array([51.5, -33.9, 0.0]), np.array([-0.12, 151.2, 0.0])
    x, y = to_world(lat, lon)
    assert np.allclose((x[2], y[2]), (0.5, 0.5))
    lat2, lon2 = from_world(x, y)
    assert np.allclose(lat, lat2) and np.allclose(lon, lon2)


def test_segment_lengths_one_degree_latitude():
    assert segment_lengths(np.array([[0.0, 0.0], [1.0, 0.0]]))[0] == pytest.approx(111_195, rel=1e-3)


def test_densest_cluster_ignores_far_away_outliers():
    home = np.random.default_rng(0).normal([51.5, -0.1], 0.02, size=(40, 2))
    away = np.array([[38.7, -9.1], [40.7, -74.0]])
    chosen = densest_cluster(np.vstack([home, away]))
    assert set(chosen) == set(range(40))


def test_fit_aspect_keeps_center():
    b = BBox(0.1, 0.2, 0.2, 0.25).fit_aspect(1.0)
    assert b.width == pytest.approx(b.height)
    assert b.center == pytest.approx((0.15, 0.225))


# --- parsing ---------------------------------------------------------------

def test_parse_gpx_segments_and_metadata():
    act = parse_gpx(GPX.encode())
    assert [len(s) for s in act.segments] == [3, 2]
    assert act.name == "Morning Run"
    assert act.sport == "run"
    assert act.start_time.year == 2021


def test_parse_tcx_skips_points_without_position():
    act = parse_tcx(TCX.encode())
    assert act.sport == "run"
    assert act.segments[0].shape == (2, 2)
    assert act.start_time.isoformat().startswith("2020-01-02T08:00")


def test_load_gzipped_and_strava_csv(tmp_path):
    (tmp_path / "activities").mkdir()
    (tmp_path / "activities" / "123.gpx.gz").write_bytes(gzip.compress(GPX.replace("<type>9</type>", "").encode()))
    (tmp_path / "activities.csv").write_text(
        'Activity ID,Activity Date,Activity Name,Activity Type,Filename\n'
        '123,"Mar 4, 2022, 7:00:00 AM",Lunch Jog,Run,activities/123.gpx.gz\n')
    files = find_activity_files([tmp_path])
    assert [f.name for f in files] == ["123.gpx.gz"]
    index = load_strava_index([tmp_path])
    act = load_activity(files[0], index)
    assert act.sport == "run"
    assert act.name == "Lunch Jog"


@pytest.mark.parametrize("raw,expected", [
    ("Running", "run"), ("trail_running", "run"), ("9", "run"), ("Ride", "ride"),
    ("cycling", "ride"), ("Hike", "hike"), ("", None), (None, None), ("Yoga", "yoga"),
])
def test_normalize_sport(raw, expected):
    assert normalize_sport(raw) == expected


def test_fit_file_roundtrip(tmp_path):
    fitdecode = pytest.importorskip("fitdecode")
    del fitdecode
    data = _minimal_fit([(45.0, 7.0), (45.001, 7.001), (45.002, 7.0)])
    p = tmp_path / "run.fit"
    p.write_bytes(data)
    act = load_activity(p)
    assert act.segments[0] == pytest.approx(np.array([[45.0, 7.0], [45.001, 7.001], [45.002, 7.0]]), abs=1e-6)
    assert act.sport == "run"


def _minimal_fit(points):
    """Hand-build a tiny FIT file: a sport message and a few GPS records."""
    import struct

    def crc(data):
        table = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
                 0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]
        c = 0
        for b in data:
            for nib in (b & 0xF, b >> 4):
                tmp = table[c & 0xF]
                c = (c >> 4) & 0x0FFF
                c = c ^ tmp ^ table[nib]
        return c

    body = bytearray()
    # definition: local 0 = sport (global 12), field 0 sport enum
    body += bytes([0x40, 0, 0]) + struct.pack("<H", 12) + bytes([1, 0, 1, 0x00])
    body += bytes([0x00, 1])  # running
    # definition: local 1 = record (global 20), fields lat(0) lon(1) sint32
    body += bytes([0x41, 0, 0]) + struct.pack("<H", 20) + bytes([2, 0, 4, 0x85, 1, 4, 0x85])
    for lat, lon in points:
        body += bytes([0x01]) + struct.pack("<ii", round(lat / 180 * 2**31), round(lon / 180 * 2**31))
    header = struct.pack("<BBHI4s", 12, 0x10, 2078, len(body), b".FIT")
    data = header + bytes(body)
    return data + struct.pack("<H", crc(data))


# --- cleaning --------------------------------------------------------------

def test_clean_splits_gps_jumps_and_drops_null_island():
    seg = np.vstack([line(51.5, -0.1, 51.501, -0.1, 10), [[0.0, 0.0]], line(51.6, -0.1, 51.601, -0.1, 10)])
    act = clean_activity(Activity("x", [seg]), max_gap_m=250)
    assert [len(s) for s in act.segments] == [10, 10]


def test_trim_ends_hides_start_and_finish():
    seg = line(51.5, -0.1, 51.51, -0.1, 101)  # ~1.1 km
    trimmed = clean_activity(Activity("x", [seg]), trim_ends_m=200).segments
    total = sum(segment_lengths(s).sum() for s in trimmed)
    assert total == pytest.approx(segment_lengths(seg).sum() - 400, abs=15)
    assert trimmed[0][0, 0] > 51.5015 and trimmed[-1][-1, 0] < 51.5085


# --- rendering -------------------------------------------------------------

def _viewport():
    return Viewport(BBox.from_latlon(51.49, -0.11, 51.51, -0.09), 200, 200)


def test_accumulate_counts_each_activity_once():
    vp = _viewport()
    horiz = line(51.50, -0.108, 51.50, -0.092)
    vert = line(51.492, -0.10, 51.508, -0.10)
    # The first activity crosses the same street twice; it must still count once.
    acts = [Activity("a", [horiz, horiz[::-1]]), Activity("b", [horiz]), Activity("c", [vert])]
    acc = accumulate(acts, vp)
    cx, cy = vp.project(np.array([[51.50, -0.104]]))[0].astype(int)
    assert acc[cy - 1:cy + 2, cx].max() == pytest.approx(2.0, abs=0.05)
    assert acc.max() <= 3.0 + 1e-5


def test_tracks_outside_viewport_are_ignored():
    acc = accumulate([Activity("far", [line(10, 10, 10.1, 10.1)])], _viewport())
    assert acc.sum() == 0


def test_tone_map_brightest_where_most_run():
    acc = np.array([[0, 1, 5, 50]], dtype=np.float32)
    for scale in ("log", "linear", "equalize"):
        t = tone_map(acc, scale=scale)
        assert t[0, 0] == 0 and np.all(np.diff(t[0]) > 0) and t.max() <= 1


def test_gaussian_blur_preserves_mass():
    a = np.zeros((101, 101), dtype=np.float32)
    a[50, 50] = 1
    b = gaussian_blur(a, 4)
    assert b.sum() == pytest.approx(1.0, rel=1e-3)
    assert b[50, 50] == b.max()


def test_render_busy_street_is_brighter():
    vp = _viewport()
    busy = [Activity(str(i), [line(51.50, -0.108, 51.50, -0.092)]) for i in range(20)]
    quiet = [Activity("q", [line(51.492, -0.10, 51.497, -0.10)])]
    img = np.asarray(render(busy + quiet, vp, glow=0).convert("L"), dtype=float)
    (bx, by), (qx, qy) = vp.project(np.array([[51.50, -0.104], [51.494, -0.10]])).astype(int)
    assert img[by - 1:by + 2, bx].max() > img[qy, qx - 1:qx + 2].max() > img[5, 5]


# --- poster, basemap, web --------------------------------------------------

def test_paper_sizes():
    assert paper_pixels("a4", 300) == (2480, 3508)
    assert paper_pixels("A4", 300, landscape=True) == (3508, 2480)
    assert paper_pixels("50x70cm", 100) == (1969, 2756)
    with pytest.raises(ValueError):
        paper_pixels("napkin")


def test_layout_leaves_room_for_caption():
    lay = layout((1000, 1400))
    l, t, r, b = lay.map_box
    assert l > 0 and r < 1000 and b == lay.caption_top < 1400


def test_basemap_stitches_and_caches_tiles(tmp_path):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        buf = io.BytesIO()
        Image.new("RGB", (256, 256), (40, 80, 120)).save(buf, "PNG")
        return buf.getvalue()

    bbox = BBox.from_latlon(51.49, -0.11, 51.51, -0.09)
    img = fetch_basemap(bbox, 300, 300, "carto-dark", cache_dir=tmp_path, fetch=fake_fetch, key="K")
    assert img.size == (300, 300) and img.getpixel((150, 150)) == (40, 80, 120)
    n = len(calls)
    assert n > 0 and calls[0].endswith(".png?key=K")
    fetch_basemap(bbox, 300, 300, "carto-dark", cache_dir=tmp_path, fetch=fake_fetch, key="K")
    assert len(calls) == n  # second time everything comes from the cache
    assert 12 <= choose_zoom(bbox, 300) <= 15


def test_build_html_contains_tracks():
    html = build_html([Activity("a", [line(51.5, -0.1, 51.51, -0.1)])], title="My <Runs>")
    assert "My &lt;Runs&gt;" in html and "51.5" in html and "__" not in html


# --- CLI -------------------------------------------------------------------

def _write_runs(tmp_path):
    d = tmp_path / "runs"
    d.mkdir()
    for i in range(6):
        pts = "".join(f'<trkpt lat="{51.5 + k * 1e-4:.5f}" lon="{-0.1 + i * 1e-3:.5f}"/>' for k in range(60))
        (d / f"r{i}.gpx").write_text(f'<gpx><trk><type>running</type><trkseg>{pts}</trkseg></trk></gpx>')
    ride = "".join(f'<trkpt lat="51.5" lon="{-0.1 + k * 1e-4:.5f}"/>' for k in range(60))
    (d / "ride.gpx").write_text(f'<gpx><trk><type>cycling</type><trkseg>{ride}</trkseg></trk></gpx>')
    (d / "broken.gpx").write_text("<gpx><trk>")
    return d


def test_cli_image_and_html(tmp_path, capsys):
    d = _write_runs(tmp_path)
    out = tmp_path / "out.png"
    html = tmp_path / "map.html"
    assert cli.main([str(d), "-o", str(out), "--width", "400", "--html", str(html)]) == 0
    err = capsys.readouterr().err
    assert "Using 6 activities" in err and "1 unreadable" in err and "skipped 1" in err
    assert Image.open(out).width == 400
    assert html.read_text().count("51.5") > 6


def test_cli_poster(tmp_path):
    d = _write_runs(tmp_path)
    out = tmp_path / "poster.png"
    assert cli.main([str(d), "-o", str(out), "--poster", "a4", "--dpi", "50", "--title", "Test"]) == 0
    assert Image.open(out).size == paper_pixels("a4", 50)


def test_cli_nothing_to_draw(tmp_path):
    d = _write_runs(tmp_path)
    assert cli.main([str(d), "-o", str(tmp_path / "x.png"), "--sport", "swim", "--strict-sport"]) == 1


def test_map_keys():
    from heatmap.basemap import resolve_provider

    url, _ = resolve_provider("stadia-dark", "abc 123")
    assert url.endswith("{y}.png?api_key=abc%20123")
    url, _ = resolve_provider("stadia-dark")  # optional key is dropped
    assert url.endswith("{y}.png") and "{key}" not in url
    with pytest.raises(ValueError, match="needs an API key"):
        resolve_provider("maptiler-dark")
    with pytest.raises(ValueError, match="needs an API key"):
        resolve_provider("carto-dark")
    assert resolve_provider("carto-light", "K")[0].endswith("light_nolabels/{z}/{x}/{y}.png?key=K")
    url, _ = resolve_provider("https://t.example.com/{z}/{x}/{y}.png?token={key}", "K")
    assert url.endswith("?token=K")


def test_cli_html_map_choice(tmp_path):
    d = _write_runs(tmp_path)
    html = tmp_path / "map.html"
    args = [str(d), "-o", str(tmp_path / "o.png"), "--width", "200", "--html", str(html)]
    assert cli.main(args + ["--html-map", "none"]) == 0
    assert "const TILES = null;" in html.read_text()
    assert cli.main(args + ["--html-map", "maptiler-dark", "--map-key", "SECRET"]) == 0
    assert "dataviz-dark/256/{z}/{x}/{y}.png?key=SECRET" in html.read_text()
    assert cli.main(args) == 0  # CARTO without a key: page still written, no map
    assert "const TILES = null;" in html.read_text()
    assert cli.main(args + ["--map-key", "CK"]) == 0
    assert "basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png?key=CK" in html.read_text()


# --- FIT files and zip archives ----------------------------------------------

DATA = __import__("pathlib").Path(__file__).parent / "data"


def test_garmin_style_fit():
    act = load_activity(DATA / "garmin_run.fit")
    assert act.sport == "run"
    assert act.start_time.year == 2024
    # 200 records, some without a GPS fix, as real watches write them
    assert act.segments[0].shape == (188, 2)
    assert act.segments[0][0] == pytest.approx([51.5, -0.1], abs=1e-6)


def test_generic_sport_counts_as_unknown():
    assert load_activity(DATA / "garmin_generic.fit").sport is None
    assert normalize_sport("generic") is None


def test_truncated_fit_keeps_points(tmp_path):
    data = (DATA / "garmin_run.fit").read_bytes()
    p = tmp_path / "cut.fit"
    p.write_bytes(data[: len(data) // 2])
    assert len(load_activity(p).segments[0]) > 20


def test_nested_zip_like_garmin_export(tmp_path):
    import zipfile

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(DATA / "garmin_run.fit", "me_1.fit")
        z.writestr("me_2.gpx", GPX)
        z.writestr("__MACOSX/._me_1.fit", b"junk")
    outer = tmp_path / "export.zip"
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_0-_Part1.zip", inner.getvalue())
        z.writestr("DI_CONNECT/other.json", "{}")

    sources = find_activity_files([outer])
    assert sorted(s.name for s in sources) == ["me_1.fit", "me_2.gpx"]
    from heatmap.parsers import iter_activities
    results = dict((s.name, a) for s, a in iter_activities(sources))
    assert results["me_1.fit"].sport == "run" and results["me_2.gpx"].sport == "run"


def test_cli_reads_gpx_and_fit_together(tmp_path, capsys):
    d = _write_runs(tmp_path)
    (d / "watch.fit").write_bytes((DATA / "garmin_run.fit").read_bytes())
    assert cli.main([str(d), "-o", str(tmp_path / "o.png"), "--width", "300"]) == 0
    assert "Using 7 activities" in capsys.readouterr().err


def test_cli_single_straight_route_has_sane_size(tmp_path):
    out = tmp_path / "o.png"
    assert cli.main([str(DATA / "garmin_run.fit"), "-o", str(out), "--width", "300"]) == 0
    assert Image.open(out).size == (300, 600)


# --- street map under the runs -----------------------------------------------

def test_reveal_brightens_dark_streets_but_not_background():
    from heatmap.basemap import reveal

    img = Image.new("RGB", (100, 100), (14, 14, 14))
    for x in range(0, 100, 10):  # faint streets, as in dark tile styles
        img.paste((38, 38, 38), (x, 0, x + 1, 100))
    out = np.asarray(reveal(img))
    assert out[50, 55, 0] == 14            # background unchanged
    assert out[50, 0, 0] >= 70             # streets now clearly visible
    light = Image.new("RGB", (10, 10), (240, 240, 240))
    assert reveal(light) is light          # light maps left alone


def test_runs_stay_brighter_than_streets_on_a_map():
    vp = _viewport()
    base = Image.new("RGB", (vp.width, vp.height), (77, 77, 77))  # a lit-up street everywhere
    once = [Activity("q", [line(51.50, -0.108, 51.50, -0.092)])]
    img = np.asarray(render(once, vp, glow=0, base=base).convert("L"), dtype=float)
    x, y = vp.project(np.array([[51.50, -0.10]]))[0].astype(int)
    assert img[y - 1:y + 2, x].max() > 77 + 20


def test_poster_uses_a_street_map_by_default(tmp_path, offline_basemap):
    d = _write_runs(tmp_path)
    args = [str(d), "-o", str(tmp_path / "p.png"), "--poster", "a4", "--dpi", "40", "--map-key", "K"]
    assert cli.main(args) == 0
    assert cli.main(args + ["--palette", "ink"]) == 0
    assert cli.main(args + ["--basemap", "none"]) == 0
    assert offline_basemap == ["carto-dark", "carto-light"]
