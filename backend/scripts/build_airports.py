"""Build the committed airport reference file from OurAirports.

Downloads (or reads) the OurAirports `airports.csv`, keeps airports that have an
IATA code and scheduled passenger service, looks up each airport's IANA time zone
from its coordinates, and writes `src/flightsearch/data/airports.csv`.

Ryanair's airport list (a test fixture) is used to:
  * force-include every airport Ryanair serves, even if OurAirports types it as small;
  * cross-check the computed time zones against the zones Ryanair reports.

Usage (from backend/):
    uv run python scripts/build_airports.py
    uv run python scripts/build_airports.py --source path/to/airports.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
from timezonefinder import TimezoneFinder

BACKEND_DIR = Path(__file__).resolve().parent.parent
OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
DEFAULT_OUTPUT = BACKEND_DIR / "src" / "flightsearch" / "data" / "airports.csv"
DEFAULT_RYANAIR = BACKEND_DIR / "tests" / "fixtures" / "ryanair" / "airports.json"

# Airport types kept by default; Ryanair airports are kept regardless of type.
KEPT_TYPES = {"large_airport", "medium_airport"}
# When OurAirports has several rows with the same IATA code, prefer the bigger one.
TYPE_RANK = {"large_airport": 3, "medium_airport": 2, "small_airport": 1}

# Airports where Ryanair's reported time zone is known to be wrong.
# BEM is Beni Mellal, Morocco (Africa/Casablanca); Ryanair says Africa/Bangui.
KNOWN_RYANAIR_TZ_ERRORS = {"BEM"}

OUTPUT_COLUMNS =["iata", "name", "city", "country_code", "latitude", "longitude", "timezone"]


@dataclass(frozen=True)
class AirportRow:
    iata: str
    name: str
    city: str
    country_code: str
    latitude: float
    longitude: float
    timezone: str
    type: str


def load_source(source: str) -> str:
    """Return the OurAirports CSV text from a URL or a local file path."""
    if source.startswith(("http://", "https://")):
        print(f"Downloading {source} ...", file=sys.stderr)
        response = httpx.get(source, timeout=60, follow_redirects=True)
        response.raise_for_status()
        return response.text
    return Path(source).read_text(encoding="utf-8")


def load_ryanair_zones(path: Path) -> dict[str, str]:
    """Map IATA code -> IANA time zone from Ryanair's airport list."""
    if not path.exists():
        print(f"Ryanair list not found at {path}; skipping cross-check.", file=sys.stderr)
        return {}
    airports = json.loads(path.read_text(encoding="utf-8"))
    return {a["code"]: a["timeZone"] for a in airports if a.get("code") and a.get("timeZone")}


def select_rows(csv_text: str, force_include: set[str], tf: TimezoneFinder) -> list[AirportRow]:
    """Filter OurAirports rows and attach time zones. One row per IATA code."""
    by_iata: dict[str, AirportRow] = {}
    no_timezone: list[str] = []

    for raw in csv.DictReader(io.StringIO(csv_text)):
        iata = raw["iata_code"].strip().upper()
        if len(iata) != 3 or not iata.isalpha():
            continue
        scheduled = raw["scheduled_service"] == "yes" and raw["type"] in KEPT_TYPES
        if not scheduled and iata not in force_include:
            continue
        if raw["type"] == "closed":
            continue

        lat = float(raw["latitude_deg"])
        lon = float(raw["longitude_deg"])
        tz = tf.timezone_at(lng=lon, lat=lat)
        if tz is None:
            no_timezone.append(iata)
            continue

        row = AirportRow(
            iata=iata,
            name=raw["name"].strip(),
            city=raw["municipality"].strip(),
            country_code=raw["iso_country"].strip().upper(),
            latitude=round(lat, 4),
            longitude=round(lon, 4),
            timezone=tz,
            type=raw["type"],
        )
        existing = by_iata.get(iata)
        if existing is None or TYPE_RANK.get(row.type, 0) > TYPE_RANK.get(existing.type, 0):
            by_iata[iata] = row

    if no_timezone:
        print(f"WARNING: no time zone found for {sorted(no_timezone)}", file=sys.stderr)
    return sorted(by_iata.values(), key=lambda r: r.iata)


def cross_check(rows: list[AirportRow], ryanair_zones: dict[str, str]) -> int:
    """Report disagreements with Ryanair. Returns the number of problems found."""
    ours = {r.iata: r for r in rows}
    problems = 0

    missing = sorted(set(ryanair_zones) - set(ours))
    if missing:
        problems += len(missing)
        print(f"Ryanair airports missing from output: {missing}", file=sys.stderr)

    for iata, ryanair_tz in sorted(ryanair_zones.items()):
        row = ours.get(iata)
        if iata in KNOWN_RYANAIR_TZ_ERRORS:
            continue
        if row is not None and row.timezone != ryanair_tz:
            problems += 1
            print(
                f"Time zone mismatch {iata}: computed {row.timezone}, Ryanair {ryanair_tz}",
                file=sys.stderr,
            )

    ocean = [r.iata for r in rows if r.timezone.startswith("Etc/")]
    if ocean:
        problems += len(ocean)
        print(f"Airports resolved to an Etc/ (ocean) zone: {ocean}", file=sys.stderr)

    return problems


def write_csv(rows: list[AirportRow], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_COLUMNS)
        for r in rows:
            writer.writerow(
                [r.iata, r.name, r.city, r.country_code, r.latitude, r.longitude, r.timezone]
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default=OURAIRPORTS_URL, help="OurAirports CSV URL or path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ryanair", type=Path, default=DEFAULT_RYANAIR)
    args = parser.parse_args()

    ryanair_zones = load_ryanair_zones(args.ryanair)
    rows = select_rows(load_source(args.source), set(ryanair_zones), TimezoneFinder())
    problems = cross_check(rows, ryanair_zones)
    write_csv(rows, args.output)

    print(f"Wrote {len(rows)} airports to {args.output}", file=sys.stderr)
    if problems:
        print(f"{problems} problem(s) found; review before committing.", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
