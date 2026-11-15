from zoneinfo import ZoneInfo

import pytest

from flightsearch.domain import Airport, great_circle_km, normalize_iata


def make_airport(**overrides: object) -> Airport:
    fields: dict[str, object] = {
        "iata": "VAR",
        "name": "Varna Airport",
        "city": "Varna",
        "country_code": "BG",
        "latitude": 43.2321,
        "longitude": 27.8251,
        "timezone": ZoneInfo("Europe/Sofia"),
    }
    fields.update(overrides)
    return Airport(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(("raw", "expected"), [("VAR", "VAR"), ("var", "VAR"), (" bgy ", "BGY")])
def test_normalize_iata_accepts_codes(raw: str, expected: str) -> None:
    assert normalize_iata(raw) == expected


@pytest.mark.parametrize("raw", ["", "VA", "VARN", "V4R", "ÄBC"])
def test_normalize_iata_rejects_invalid_codes(raw: str) -> None:
    with pytest.raises(ValueError, match="Invalid IATA"):
        normalize_iata(raw)


def test_airport_is_immutable() -> None:
    airport = make_airport()
    with pytest.raises(AttributeError):
        airport.iata = "SOF"  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"iata": "var"},
        {"iata": "VARN"},
        {"latitude": 91.0},
        {"longitude": -181.0},
        {"country_code": "BGR"},
    ],
)
def test_airport_rejects_invalid_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        make_airport(**overrides)


def test_great_circle_zero_for_same_point() -> None:
    assert great_circle_km(43.0, 27.0, 43.0, 27.0) == 0.0


def test_great_circle_known_distance() -> None:
    # One degree of latitude along a meridian is ~111.2 km.
    assert great_circle_km(0.0, 0.0, 1.0, 0.0) == pytest.approx(111.2, abs=0.1)


def test_distance_km_between_airports_is_symmetric() -> None:
    varna = make_airport()
    sofia = make_airport(iata="SOF", latitude=42.6964, longitude=23.4177)
    assert varna.distance_km(sofia) == pytest.approx(sofia.distance_km(varna))
    # Varna-Sofia airports are roughly 360 km apart in a straight line.
    assert 340 < varna.distance_km(sofia) < 380
