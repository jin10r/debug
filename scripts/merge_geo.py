#!/usr/bin/env python3
"""Merge streets.csv + settlements.csv into geo.csv with 8-class typification.

Types: street, village, town, park, market, station, infrastructure, landmark.
Untyped entries sort to end of file for manual review.
"""

import csv
import io
import subprocess
import sys
from pathlib import Path

SRC_STREETS = Path("postgres/data/streets.csv")
SRC_SETTLEMENTS = None  # loaded from git
DST = Path("postgres/data/geo.csv")

# ── Settlement type mapping ────────────────────────────────────────────────
PLACE_MAP = {
    "village": "village",
    "town": "town",
    "city": "town",  # no 'city' type → downgrade to town
}

# ── Street reclassification heuristics (high-confidence only) ──────────────
# Primary name (first alias, lowercased): if any key is a substring of the
# first pipe-delimited alias, the entry is reclassified.
RE_KEYWORDS: list[tuple[str, str]] = [
    # market
    ("рынок", "market"),
    ("базар", "market"),
    # station — проверяем все псевдонимы, а не только первый
    ("станция", "station"),
    ("автостанция", "station"),
    ("аэропорт", "station"),
    # infrastructure — вокзал это infra, а не station
    ("вокзал", "infrastructure"),
    ("больница", "infrastructure"),
    ("госпиталь", "infrastructure"),
    ("поликлиника", "infrastructure"),
    # park
    ("парк горького", "park"),
    ("парк ильича", "park"),
    ("парк космонавтов", "park"),
    ("парк шевченко", "park"),
    ("парк энтузиастов", "park"),
    ("парк юность", "park"),
    ("парк победы", "park"),
    ("сквер", "park"),
    ("лунапарк", "park"),
    ("стамбульский парк", "park"),
    ("савицкий парк", "park"),
    ("артиллерийский парк", "park"),
    ("скейт-парк", "park"),
    ("скейтпарк", "park"),
    ("зоопарк", "park"),
    # landmark
    ("площадь", "landmark"),
    ("кладбище", "landmark"),
    ("собор", "landmark"),
]

# ── Helpers ────────────────────────────────────────────────────────────────


def load_settlements_from_git(commit: str = "485741d") -> list[dict]:
    """Load settlements.csv from a git commit (it's not in the working tree)."""
    raw = subprocess.run(
        ["git", "show", f"{commit}:postgres/data/settlements.csv"],
        capture_output=True, text=True, check=True,
    ).stdout
    reader = csv.DictReader(io.StringIO(raw), fieldnames=["names", "wkt_geom", "place"])
    next(reader)  # skip header
    rows = []
    for i, row in enumerate(reader, start=1):
        place = row["place"].strip().strip('"')
        rows.append({
            "id": i,
            "names": row["names"],
            "wkt_geom": row["wkt_geom"],
            "place": place,
        })
    return rows


def classify_settlement(place: str) -> str | None:
    return PLACE_MAP.get(place) if place else None


def classify_street(names: str) -> str | None:
    """Return reclassified type or None for default 'street'.
    Checks ALL pipe-delimited aliases to catch cases like
    '1 Люстдорфской|1-я станция Люстдорфская'.
    """
    all_aliases = names.lower()
    for kw, t in RE_KEYWORDS:
        if kw in all_aliases:
            return t
    return None


def merge_and_write(streets_rows: list[dict], settlements_rows: list[dict], dst: Path):
    """Merge rows, sort typed first then untyped, write CSV."""
    all_rows: list[dict] = []

    for row in streets_rows:
        t = classify_street(row["names"])
        all_rows.append({
            "names": row["names"],
            "wkt_geom": row["wkt_geom"],
            "type": t if t else "street",
        })

    for row in settlements_rows:
        t = classify_settlement(row["place"])
        all_rows.append({
            "names": row["names"],
            "wkt_geom": row["wkt_geom"],
            "type": t,
        })

    # Sort: typed (non-None) first alphabetically by name, then untyped
    typed = [r for r in all_rows if r["type"] is not None]
    untyped = [r for r in all_rows if r["type"] is None]
    typed.sort(key=lambda r: r["names"].lower().split("|")[0])
    untyped.sort(key=lambda r: r["names"].lower().split("|")[0])

    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["names", "wkt_geom", "type"])
        for row in typed:
            writer.writerow([row["names"], row["wkt_geom"], row["type"]])
        for row in untyped:
            writer.writerow([row["names"], row["wkt_geom"], ""])

    print(f"geo.csv: {len(typed)} typed + {len(untyped)} untyped = {len(all_rows)} total")


def main():
    # 1. Load streets
    if not SRC_STREETS.exists():
        print(f"ERROR: {SRC_STREETS} not found", file=sys.stderr)
        sys.exit(1)

    with open(SRC_STREETS, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        streets_rows = list(reader)
    print(f"Loaded {len(streets_rows)} streets")

    # 2. Load settlements from git
    try:
        settlements_rows = load_settlements_from_git()
        print(f"Loaded {len(settlements_rows)} settlements from git")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: could not load settlements from git: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Merge & write
    merge_and_write(streets_rows, settlements_rows, DST)


if __name__ == "__main__":
    main()
