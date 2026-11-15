from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from flightsearch.reference import (
    AirportDataError,
    InMemoryAirportRepository,
    UnknownAirportError,
)

HEADER = "iata,name,city,country_code,latitude,longitude,timezone\n"


@pytest.fixture(scope="module")
def airports() -> InMemoryAirportRepository:
    """The real, packaged airport file."""
    return InMemoryAirportRepository.load_default()


def write_csv(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "airports.csv"
    path.write_text(HEADER + body, encoding="utf-8")
    return path


# --- The packaged data file ---


def test_default_file_loads(airports: InMemoryAirportRepository) -> None:
    assert len(airports.all()) > 3000


@pytest.mark.parametrize(
    ("code", "timezone"),
    [
        ("VAR", "Europe/Sofia"),
        ("SOF", "Europe/Sofia"),
        ("BGY", "Europe/Rome"),
        ("MXP", "Europe/Rome"),
        ("LIN", "Europe/Rome"),  # not a Ryanair airport, must still be present
    ],
)
def test_home_and_milan_airports_have_correct_timezones(
    airports: InMemoryAirportRepository, code: str, timezone: str
) -> None:
    assert airports.get(code).timezone == ZoneInfo(timezone)


def test_lookup_is_case_insensitive(airports: InMemoryAirportRepository) -> None:
    assert airports.get("var") is airports.get("VAR")
    assert airports.find("sof") is not None


def test_get_unknown_code_raises(airports: InMemoryAirportRepository) -> None:
    with pytest.raises(UnknownAirportError, match="'XXX'"):
        airports.get("XXX")


def test_find_returns_none_for_unknown_or_malformed(airports: InMemoryAirportRepository) -> None:
    assert airports.find("XXX") is None
    assert airports.find("not a code") is None
    assert airports.find("not a code") is None


def test_all_is_sorted_by_code(airports: InMemoryAirportRepository) -> None:
    codes = [a.iata for a in airports.all()]
    assert codes == sorted(codes)


def test_radius_around_varna(airports: InMemoryAirportRepository) -> None:
    varna = airports.get("VAR")
    nearby = airports.within_radius(varna.latitude, varna.longitude, 150)
    codes = [a.iata for a in nearby]
    assert codes[0] == "VAR"  # nearest first, distance 0
    assert "BOJ" in codes  # Burgas, ~100 km
    assert "SOF" not in codes  # Sofia, ~360 km


# --- Behaviour on small, controlled inputs ---


def test_radius_is_inclusive_and_sorted(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "AAA,Far,Far,BG,2.0,0.0,Europe/Sofia\n"
        "BBB,Near,Near,BG,1.0,0.0,Europe/Sofia\n"
        "CCC,Origin,Origin,BG,0.0,0.0,Europe/Sofia\n",
    )
    repo = InMemoryAirportRepository.from_csv(path)
    # BBB is ~111.2 km away; AAA ~222.4 km.
    assert [a.iata for a in repo.within_radius(0.0, 0.0, 112)] == ["CCC", "BBB"]
    assert [a.iata for a in repo.within_radius(0.0, 0.0, 0)] == ["CCC"]


def test_negative_radius_rejected(tmp_path: Path) -> None:
    repo = InMemoryAirportRepository.from_csv(write_csv(tmp_path, ""))
    with pytest.raises(ValueError):
        repo.within_radius(0.0, 0.0, -1)


@pytest.mark.parametrize(
    "bad_row",
    [
        "VAR,Varna,Varna,BG,not-a-number,27.8,Europe/Sofia\n",
        "VAR,Varna,Varna,BG,43.2,27.8,Mars/Olympus_Mons\n",
        "VAR,Varna,Varna,BG,43.2,27.8\n",  # missing timezone column
        "V4R,Varna,Varna,BG,43.2,27.8,Europe/Sofia\n",
    ],
)
def test_malformed_rows_report_line_number(tmp_path: Path, bad_row: str) -> None:
    good_row = "SOF,Sofia,Sofia,BG,42.7,23.4,Europe/Sofia\n"
    path = write_csv(tmp_path, good_row + bad_row)
    with pytest.raises(AirportDataError, match="line 3"):
        InMemoryAirportRepository.from_csv(path)


def test_duplicate_codes_rejected(tmp_path: Path) -> None:
    row = "VAR,Varna,Varna,BG,43.2,27.8,Europe/Sofia\n"
    with pytest.raises(AirportDataError, match="Duplicate"):
        InMemoryAirportRepository.from_csv(write_csv(tmp_path, row + row))
