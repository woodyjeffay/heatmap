"""Geographic helpers: Web Mercator projection, distances and region selection."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

EARTH_RADIUS_M = 6_371_008.8
MAX_LAT = 85.05112878  # Web Mercator cut-off


def to_world(lat, lon):
    """Project lat/lon (degrees) to normalized Web Mercator coordinates.

    Returns ``(x, y)`` in ``[0, 1]``, with ``y`` growing southwards, which is
    the same convention slippy-map tiles use.
    """
    lat = np.clip(np.asarray(lat, dtype=np.float64), -MAX_LAT, MAX_LAT)
    lon = np.asarray(lon, dtype=np.float64)
    x = (lon + 180.0) / 360.0
    s = np.sin(np.radians(lat))
    y = 0.5 - np.log((1 + s) / (1 - s)) / (4 * math.pi)
    return x, y


def from_world(x, y):
    """Inverse of :func:`to_world`."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    lon = x * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * y))))
    return lat, lon


def segment_lengths(latlon: np.ndarray) -> np.ndarray:
    """Great-circle length (metres) of each consecutive pair of points."""
    if len(latlon) < 2:
        return np.zeros(0)
    lat = np.radians(latlon[:, 0])
    lon = np.radians(latlon[:, 1])
    dlat = np.diff(lat)
    dlon = np.diff(lon)
    a = np.sin(dlat / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


@dataclass(frozen=True)
class BBox:
    """A rectangle in normalized world (Web Mercator) coordinates."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2

    @classmethod
    def from_latlon(cls, lat0, lon0, lat1, lon1) -> "BBox":
        xa, ya = to_world([lat0, lat1], [lon0, lon1])
        return cls(float(min(xa)), float(min(ya)), float(max(xa)), float(max(ya)))

    @classmethod
    def around(cls, lat: float, lon: float, radius_km: float) -> "BBox":
        """A square box of ``radius_km`` around a point (in ground distance)."""
        dlat = math.degrees(radius_km * 1000 / EARTH_RADIUS_M)
        dlon = dlat / max(math.cos(math.radians(lat)), 1e-6)
        return cls.from_latlon(lat - dlat, lon - dlon, lat + dlat, lon + dlon)

    def pad(self, fraction: float) -> "BBox":
        dx = self.width * fraction
        dy = self.height * fraction
        return BBox(self.x0 - dx, self.y0 - dy, self.x1 + dx, self.y1 + dy)

    def fit_aspect(self, aspect: float) -> "BBox":
        """Grow the box (keeping its centre) until width / height == aspect."""
        cx, cy = self.center
        w, h = max(self.width, 1e-12), max(self.height, 1e-12)
        if w / h < aspect:
            w = h * aspect
        else:
            h = w / aspect
        return BBox(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def bbox_of(arrays: list[np.ndarray]) -> BBox:
    """Bounding box of a list of ``(N, 2)`` world-coordinate arrays."""
    xs = np.concatenate([a[:, 0] for a in arrays])
    ys = np.concatenate([a[:, 1] for a in arrays])
    return BBox(float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))


def densest_cluster(centers_latlon: np.ndarray, cell_deg: float = 0.25) -> np.ndarray:
    """Pick the geographic cluster containing the most activities.

    ``centers_latlon`` holds one representative point per activity. Activities
    are binned into a coarse grid (``cell_deg`` degrees, ~25 km); touching
    occupied cells form clusters, and the indices of the activities in the
    most populated cluster are returned. This keeps a single holiday run on
    another continent from shrinking your home city to a dot.
    """
    if len(centers_latlon) == 0:
        return np.zeros(0, dtype=int)
    cells = np.floor(centers_latlon / cell_deg).astype(np.int64)
    counts: dict[tuple[int, int], int] = {}
    for c in map(tuple, cells):
        counts[c] = counts.get(c, 0) + 1

    seen: set[tuple[int, int]] = set()
    best: set[tuple[int, int]] = set()
    best_count = -1
    for start in counts:
        if start in seen:
            continue
        component = set()
        stack = [start]
        seen.add(start)
        while stack:
            cy, cx = stack.pop()
            component.add((cy, cx))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    n = (cy + dy, cx + dx)
                    if n in counts and n not in seen:
                        seen.add(n)
                        stack.append(n)
        total = sum(counts[c] for c in component)
        if total > best_count:
            best, best_count = component, total

    return np.array([i for i, c in enumerate(map(tuple, cells)) if c in best], dtype=int)
