"""Generate a folder of fake runs so you can try run-heatmap without an export.

    python examples/make_demo_data.py demo-runs
    run-heatmap demo-runs -o demo.png

The runs wander a made-up street grid around a "home" point, with a favourite
park loop and a riverside path that get run far more than anything else,
which is exactly what a real heatmap tends to look like.
"""

from __future__ import annotations

import math
import random
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = (51.5155, -0.0922)  # somewhere in a city
N = 26                      # grid is N x N intersections
BLOCK_M = 140


def build_city(rng: random.Random):
    """Intersections (lat, lon) and an adjacency list for a jittered grid."""
    m_lat = 1 / 111_320
    m_lon = 1 / (111_320 * math.cos(math.radians(HOME[0])))
    rot = math.radians(17)
    nodes = {}
    for i in range(N):
        for j in range(N):
            x = (i - N / 2) * BLOCK_M + rng.uniform(-25, 25)
            y = (j - N / 2) * BLOCK_M + rng.uniform(-25, 25)
            # a gentle bend, like streets following an old river
            y += 180 * math.sin(x / 900)
            xr = x * math.cos(rot) - y * math.sin(rot)
            yr = x * math.sin(rot) + y * math.cos(rot)
            nodes[(i, j)] = (HOME[0] + yr * m_lat, HOME[1] + xr * m_lon)
    adj = {k: [] for k in nodes}
    for (i, j) in nodes:
        for di, dj in ((1, 0), (0, 1), (1, 1)):
            n = (i + di, j + dj)
            if n not in nodes:
                continue
            if (di, dj) == (1, 1) and not (i == j or i + 5 == j):  # two diagonal avenues
                continue
            if (di, dj) != (1, 1) and rng.random() < 0.12:  # dead ends & missing links
                continue
            adj[(i, j)].append(n)
            adj[n].append((i, j))
    return nodes, adj, (m_lat, m_lon)


def shortest(adj, a, b):
    prev = {a: None}
    q = deque([a])
    while q:
        u = q.popleft()
        if u == b:
            break
        for v in adj[u]:
            if v not in prev:
                prev[v] = u
                q.append(v)
    path = []
    while b is not None:
        path.append(b)
        b = prev.get(b)
    return path[::-1]


def street_run(rng, nodes, adj, home, km):
    route = [home]
    u = home
    dist = 0.0
    heading = None
    while dist < km * 1000 / 2:
        options = adj[u]
        # prefer going straight on, like people do
        weights = [3.0 if heading and (v[0] - u[0], v[1] - u[1]) == heading else 1.0 for v in options]
        v = rng.choices(options, weights)[0]
        heading = (v[0] - u[0], v[1] - u[1])
        route.append(v)
        dist += BLOCK_M
        u = v
    route += shortest(adj, u, home)[1:]
    return [nodes[k] for k in route]


def park_loop(scale, laps):
    lat0, lon0 = HOME[0] + 700 * scale[0], HOME[1] + 400 * scale[1]
    pts = []
    for k in range(int(80 * laps)):
        t = 2 * math.pi * k / 80
        pts.append((lat0 + 420 * math.sin(t) * scale[0], lon0 + 650 * math.cos(t) * scale[1]))
    return pts


def river_path(scale, km):
    pts = []
    for k in range(int(km * 1000 / 2 / 25)):
        x = -1800 + k * 25
        y = -1500 + 260 * math.sin(x / 700)
        pts.append((HOME[0] + y * scale[0], HOME[1] + x * scale[1]))
    return pts + pts[::-1]


def densify(points, step_m, scale, rng):
    """Interpolate to ~GPS sample spacing and add a little jitter."""
    out = []
    for (a, b) in zip(points, points[1:]):
        dy = (b[0] - a[0]) / scale[0]
        dx = (b[1] - a[1]) / scale[1]
        n = max(1, int(math.hypot(dx, dy) / step_m))
        for k in range(n):
            t = k / n
            out.append((a[0] + (b[0] - a[0]) * t + rng.gauss(0, 3) * scale[0],
                        a[1] + (b[1] - a[1]) * t + rng.gauss(0, 3) * scale[1]))
    out.append(points[-1])
    return out


def write_gpx(path: Path, pts, start: datetime, sport: str = "running"):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="run-heatmap demo" xmlns="http://www.topografix.com/GPX/1/1">',
             f"<trk><name>Demo {sport}</name><type>{sport}</type><trkseg>"]
    for k, (lat, lon) in enumerate(pts):
        t = (start + timedelta(seconds=3 * k)).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f'<trkpt lat="{lat:.6f}" lon="{lon:.6f}"><time>{t}</time></trkpt>')
    lines.append("</trkseg></trk></gpx>")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(out_dir: str = "demo-runs", count: int = 420, seed: int = 7) -> None:
    rng = random.Random(seed)
    nodes, adj, scale = build_city(rng)
    home = (N // 2, N // 2)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    day = datetime(2019, 3, 2, 7, 0, tzinfo=timezone.utc)
    for n in range(count):
        kind = rng.random()
        start_leg = [nodes[k] for k in shortest(adj, home, (N // 2 + 5, N // 2 + 3))]
        if kind < 0.25:
            pts = start_leg + park_loop(scale, rng.choice([1, 2, 3])) + start_leg[::-1]
        elif kind < 0.40:
            pts = river_path(scale, rng.uniform(5, 9))
        else:
            pts = street_run(rng, nodes, adj, home, rng.uniform(4, 16))
        write_gpx(out / f"run_{n:04d}.gpx", densify(pts, 12, scale, rng), day)
        day += timedelta(days=rng.choice([1, 2, 2, 3, 4, 7]))
    # a holiday run far away, which --region auto should ignore
    far = [(38.7223 + 0.0004 * k, -9.1393 + 0.0005 * math.sin(k / 9)) for k in range(300)]
    write_gpx(out / "holiday_lisbon.gpx", far, day)
    # a bike ride, which the default --sport run filter should skip
    write_gpx(out / "commute_ride.gpx", river_path(scale, 20), day, sport="cycling")
    print(f"Wrote {count + 2} activities to {out}/")


if __name__ == "__main__":
    main(*sys.argv[1:2])
