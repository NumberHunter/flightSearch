from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pytest

from flightsearch.domain import (
    DEFAULT_MAX_LAYOVER,
    DEFAULT_MIN_CONNECTION,
    AirportSpec,
    BeThereOn,
    DateWindow,
    ExactDates,
    FlexibleDates,
    GroupSpec,
    RadiusSpec,
    SearchRequest,
    StayRange,
)

DEC_1 = date(2026, 12, 1)
DEC_10 = date(2026, 12, 10)
DEC_20 = date(2026, 12, 20)

VAR = AirportSpec("VAR")
SOF = AirportSpec("SOF")
MILAN = GroupSpec("MIL")


def request(**overrides: object) -> SearchRequest:
    fields: dict[str, object] = {
        "origin": (VAR, SOF),
        "destination": (MILAN,),
        "dates": FlexibleDates(DateWindow(DEC_1, DEC_20), StayRange(3, 7)),
    }
    fields.update(overrides)
    return SearchRequest(**fields)  # type: ignore[arg-type]


# --- Location specs --------------------------------------------------------------------


def test_airport_spec_normalizes_the_code() -> None:
    assert AirportSpec(" var ") == VAR


def test_airport_spec_rejects_an_invalid_code() -> None:
    with pytest.raises(ValueError, match="IATA"):
        AirportSpec("VARNA")


def test_group_spec_normalizes_the_name() -> None:
    assert GroupSpec(" mil ") == MILAN


def test_group_spec_rejects_an_empty_name() -> None:
    with pytest.raises(ValueError, match="empty"):
        GroupSpec("  ")


def test_radius_spec() -> None:
    spec = RadiusSpec("var", 150)
    assert spec.code == "VAR"
    assert spec.km == 150


@pytest.mark.parametrize("km", [0, -10, float("nan"), float("inf")])
def test_radius_spec_rejects_a_bad_radius(km: float) -> None:
    with pytest.raises(ValueError, match="Radius"):
        RadiusSpec("VAR", km)


# --- DateWindow and StayRange ----------------------------------------------------------


def test_date_window_is_inclusive() -> None:
    window = DateWindow(DEC_1, DEC_20)
    assert DEC_1 in window
    assert DEC_20 in window
    assert date(2026, 11, 30) not in window
    assert date(2026, 12, 21) not in window
    assert window.length == 20


def test_date_window_days() -> None:
    days = list(DateWindow(DEC_1, date(2026, 12, 3)).days())
    assert days == [DEC_1, date(2026, 12, 2), date(2026, 12, 3)]


def test_single_day_window() -> None:
    window = DateWindow.single(DEC_10)
    assert window.length == 1
    assert list(window.days()) == [DEC_10]


def test_date_window_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="before it starts"):
        DateWindow(DEC_20, DEC_1)


def test_stay_range_is_inclusive() -> None:
    stay = StayRange(3, 7)
    assert 3 in stay
    assert 7 in stay
    assert 2 not in stay
    assert 8 not in stay


def test_stay_range_allows_same_day_return() -> None:
    assert 0 in StayRange(0, 0)


def test_stay_range_rejects_negative_nights() -> None:
    with pytest.raises(ValueError, match="negative"):
        StayRange(-1, 3)


def test_stay_range_rejects_max_below_min() -> None:
    with pytest.raises(ValueError, match="less than"):
        StayRange(7, 3)


# --- ExactDates ------------------------------------------------------------------------


def test_exact_dates_one_way() -> None:
    assert not ExactDates(DEC_10).is_round_trip


def test_exact_dates_round_trip_including_same_day() -> None:
    assert ExactDates(DEC_10, DEC_20).is_round_trip
    assert ExactDates(DEC_10, DEC_10).is_round_trip


def test_exact_dates_rejects_return_before_outbound() -> None:
    with pytest.raises(ValueError, match="before outbound"):
        ExactDates(DEC_20, DEC_10)


# --- FlexibleDates ---------------------------------------------------------------------


def test_flexible_dates_one_way_without_stay_or_return_window() -> None:
    assert not FlexibleDates(DateWindow(DEC_1, DEC_20)).is_round_trip


def test_flexible_dates_round_trip_with_stay() -> None:
    assert FlexibleDates(DateWindow(DEC_1, DEC_20), StayRange(3, 7)).is_round_trip


def test_flexible_dates_round_trip_with_only_a_return_window() -> None:
    dates = FlexibleDates(DateWindow(DEC_1, DEC_10), return_window=DateWindow(DEC_10, DEC_20))
    assert dates.is_round_trip


def test_flexible_dates_rejects_return_window_before_departures() -> None:
    with pytest.raises(ValueError, match="earliest possible return"):
        FlexibleDates(DateWindow(DEC_10, DEC_20), return_window=DateWindow(DEC_1, DEC_1))


def test_flexible_dates_rejects_return_window_too_early_for_min_stay() -> None:
    # Leaving on the 1st at the earliest with 10+ nights means returning on the 11th at the
    # earliest, but the return window ends on the 10th.
    with pytest.raises(ValueError, match="earliest possible return"):
        FlexibleDates(
            DateWindow(DEC_1, DEC_10),
            StayRange(10, 14),
            DateWindow(date(2026, 12, 5), DEC_10),
        )


# --- BeThereOn -------------------------------------------------------------------------


def test_be_there_on_avoids_flying_on_the_target_date() -> None:
    dates = BeThereOn(DEC_10)
    assert dates.latest_arrival_date == date(2026, 12, 9)
    assert dates.earliest_return_date == date(2026, 12, 11)
    assert dates.is_round_trip


def test_be_there_on_with_stay_and_departure_window() -> None:
    dates = BeThereOn(DEC_10, StayRange(3, 7), DateWindow(DEC_1, date(2026, 12, 9)))
    assert dates.stay == StayRange(3, 7)


def test_be_there_on_accepts_a_minimum_stay_below_two_nights() -> None:
    # Harmless: the target date itself already forces at least two nights.
    BeThereOn(DEC_10, StayRange(0, 5))


def test_be_there_on_rejects_a_stay_shorter_than_two_nights() -> None:
    with pytest.raises(ValueError, match="at least 2 nights"):
        BeThereOn(DEC_10, StayRange(0, 1))


def test_be_there_on_rejects_departure_window_starting_on_the_target() -> None:
    with pytest.raises(ValueError, match="not before the target"):
        BeThereOn(DEC_10, depart=DateWindow(DEC_10, DEC_20))


# --- SearchRequest ---------------------------------------------------------------------


def test_defaults() -> None:
    search = request()
    assert search.max_stops == 0
    assert search.min_connection == DEFAULT_MIN_CONNECTION == timedelta(hours=2)
    assert search.max_layover == DEFAULT_MAX_LAYOVER == timedelta(hours=24)
    assert search.allow_different_return_airport
    assert search.currency == "EUR"
    assert search.max_results == 20
    assert search.is_round_trip


def test_one_way_follows_the_date_mode() -> None:
    assert not request(dates=ExactDates(DEC_10)).is_round_trip


def test_user_can_change_the_minimum_connection() -> None:
    assert request(min_connection=timedelta(minutes=90)).min_connection == timedelta(minutes=90)


def test_location_sets_are_coerced_to_tuples_and_deduplicated() -> None:
    search = request(origin=[VAR, SOF, AirportSpec("var")])
    assert search.origin == (VAR, SOF)


def test_requests_are_hashable_and_immutable() -> None:
    search = request()
    assert hash(search) == hash(request())
    with pytest.raises(FrozenInstanceError):
        search.max_stops = 1  # type: ignore[misc]


@pytest.mark.parametrize("side", ["origin", "destination"])
def test_rejects_an_empty_location_set(side: str) -> None:
    with pytest.raises(ValueError, match="at least one location"):
        request(**{side: ()})


def test_rejects_identical_origin_and_destination() -> None:
    with pytest.raises(ValueError, match="same"):
        request(origin=(VAR,), destination=(VAR,))


@pytest.mark.parametrize("max_stops", [-1, 2])
def test_rejects_unsupported_max_stops(max_stops: int) -> None:
    with pytest.raises(ValueError, match="max_stops"):
        request(max_stops=max_stops)


def test_rejects_an_unrealistically_short_connection() -> None:
    with pytest.raises(ValueError, match="min_connection"):
        request(min_connection=timedelta(minutes=20))


def test_rejects_max_layover_not_longer_than_min_connection() -> None:
    with pytest.raises(ValueError, match="max_layover"):
        request(min_connection=timedelta(hours=4), max_layover=timedelta(hours=4))


def test_rejects_currencies_other_than_eur_for_now() -> None:
    with pytest.raises(ValueError, match="Only EUR"):
        request(currency="BGN")


@pytest.mark.parametrize("max_results", [0, 101])
def test_rejects_max_results_out_of_range(max_results: int) -> None:
    with pytest.raises(ValueError, match="max_results"):
        request(max_results=max_results)
