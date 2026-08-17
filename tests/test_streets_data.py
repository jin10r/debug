"""Data-integrity guard for postgres/data/geo.csv.

Notably guards the geometry-type restoration (commit "restore LINESTRING type for
21 degenerate street polygons"): no POLYGON may again be a near-zero-area sliver
(a street mis-encoded as a closed polygon).
"""
import csv
import math
import re
from pathlib import Path

import pytest

CSV = Path(__file__).resolve().parent.parent / "postgres" / "data" / "geo.csv"
ALLOWED = {"POINT", "LINESTRING", "POLYGON", "MULTIPOLYGON", "MULTILINESTRING", "MULTIPOINT"}
_R_LAT = 111320.0


def _rows():
    with open(CSV, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if len(row) >= 2:
                yield row[0], row[1]


def _gtype(w):
    return w.strip().split("(", 1)[0].strip().upper()


def _first_ring(w):
    m = re.search(r"\(([^()]+)\)", w)
    return m.group(1) if m else ""


def _pts(s):
    out = []
    for pair in s.split(","):
        a = pair.split()
        if len(a) >= 2:
            out.append((float(a[0]), float(a[1])))
    return out


def _area_m2(p):
    lat0 = sum(q[1] for q in p) / len(p)
    mx = _R_LAT * math.cos(math.radians(lat0))
    xs = [q[0] * mx for q in p]
    ys = [q[1] * _R_LAT for q in p]
    a = sum(xs[i] * ys[i + 1] - xs[i + 1] * ys[i] for i in range(len(p) - 1))
    return abs(a) / 2.0


def _per_m(p):
    lat0 = sum(q[1] for q in p) / len(p)
    mx = _R_LAT * math.cos(math.radians(lat0))
    return sum(
        math.hypot((p[i + 1][0] - p[i][0]) * mx, (p[i + 1][1] - p[i][1]) * _R_LAT)
        for i in range(len(p) - 1)
    )


def test_csv_exists():
    assert CSV.exists(), CSV


def test_every_row_has_three_fields():
    with open(CSV, encoding="utf-8") as f:
        r = csv.reader(f)
        header = next(r)
        assert header == ["names", "wkt_geom", "type"]
        bad = [i for i, row in enumerate(r, start=2) if len(row) != 3]
    assert bad == [], f"rows with != 3 fields: {bad[:10]}"


def test_only_allowed_geometry_types():
    bad = sorted({_gtype(w) for _, w in _rows()} - ALLOWED)
    assert bad == [], f"unexpected geometry types: {bad}"


def test_no_degenerate_sliver_polygons():
    """A POLYGON with ~0 area and ~0 compactness is a street mis-typed as polygon."""
    offenders = []
    for names, wkt in _rows():
        if _gtype(wkt) != "POLYGON":
            continue
        ring = _pts(_first_ring(wkt))
        if len(ring) < 4:
            offenders.append(names.split("|")[0])
            continue
        area = _area_m2(ring)
        per = _per_m(ring)
        comp = (4 * math.pi * area / (per * per)) if per else 0.0
        if area < 100 and comp < 0.03:
            offenders.append(names.split("|")[0])
    assert offenders == [], f"degenerate sliver polygons remain (should be LINESTRING): {offenders}"
