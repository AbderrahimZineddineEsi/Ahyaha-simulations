"""
Geometry for the demo map: distances, and things that move along a road at a steady pace.
Points are [lat, lon].
"""
import math

EARTH_M = 6_371_000


def haversine_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(h))


class Path:
    def __init__(self, points):
        self.points = [list(p) for p in points]
        self.cum = [0.0]
        for a, b in zip(self.points, self.points[1:]):
            self.cum.append(self.cum[-1] + haversine_m(a, b))
        self.length = self.cum[-1]

    def at_distance(self, d):
        d = min(max(d, 0), self.length)
        i = max(1, next((i for i, c in enumerate(self.cum) if c >= d), len(self.cum) - 1))
        seg = self.cum[i] - self.cum[i - 1]
        u = 0 if seg == 0 else (d - self.cum[i - 1]) / seg
        a, b = self.points[i - 1], self.points[i]
        return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u]

    def at(self, fraction):
        return self.at_distance(self.length * fraction)

    def from_end(self, d):
        """The point d metres before the end of the path."""
        return self.at_distance(self.length - d)


class Trip:
    """Something driving a path from `started` for `duration_s` seconds, at a steady pace."""

    def __init__(self, path, started, duration_s):
        self.path, self.started, self.duration_s = path, started, duration_s

    def progress(self, now):
        return min(1.0, max(0.0, (now - self.started) / self.duration_s))

    def position(self, now):
        return self.path.at(self.progress(now))

    def done(self, now):
        return self.progress(now) >= 1.0
