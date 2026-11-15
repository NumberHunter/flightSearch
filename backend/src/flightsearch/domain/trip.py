"""Trip: what a search returns, an outbound journey plus an optional return."""

from __future__ import annotations

from dataclasses import dataclass

from flightsearch.domain.journey import Journey
from flightsearch.domain.leg import Leg, PriceConfidence
from flightsearch.domain.money import ExchangeRates, Money, has_mixed_currencies, sum_prices


@dataclass(frozen=True, slots=True)
class Trip:
    """An outbound journey and, for a round trip, an inbound (return) journey.

    Low-cost airlines price each direction separately, so a trip is just two
    independently priced journeys; they may use different airlines and currencies.

    Which airports the journeys may use (e.g. out from SOF, back to VAR, or into BGY
    and out of MXP) is search policy, decided by the itinerary builder. This class only
    rejects trips that can't happen: returning before you've arrived.
    """

    outbound: Journey
    inbound: Journey | None = None

    def __post_init__(self) -> None:
        if self.inbound is not None and self.inbound.departure <= self.outbound.arrival:
            raise ValueError(
                f"Return departs {self.inbound.departure.isoformat()}, not after outbound "
                f"arrival {self.outbound.arrival.isoformat()}"
            )

    @property
    def is_round_trip(self) -> bool:
        return self.inbound is not None

    @property
    def journeys(self) -> tuple[Journey, ...]:
        return (self.outbound,) if self.inbound is None else (self.outbound, self.inbound)

    @property
    def legs(self) -> tuple[Leg, ...]:
        """Every leg in travel order; each one is booked separately."""
        return tuple(leg for journey in self.journeys for leg in journey.legs)

    @property
    def nights(self) -> int | None:
        """Nights at the destination, or None for a one-way trip.

        Defined as the return's departure date minus the outbound's arrival date, both
        in local time at the destination. A flight landing at 00:25 on the 15th and a
        return on the 20th is 5 nights, even though the outbound left on the 14th.
        (Proposed definition in the project brief; pending confirmation by the owner.)
        """
        if self.inbound is None:
            return None
        return (self.inbound.departure.date() - self.outbound.arrival.date()).days

    def total_price(self, rates: ExchangeRates | None = None) -> Money | None:
        """Sum of every leg's price; see `money.sum_prices` for the currency rules.

        Totalled across all legs at once, so EUR out and RON back converts both to EUR.
        """
        return sum_prices([leg.price for leg in self.legs], rates)

    @property
    def has_mixed_currencies(self) -> bool:
        return has_mixed_currencies(leg.price for leg in self.legs)

    @property
    def price_confidence(self) -> PriceConfidence:
        """EXACT only if every leg's price is exact."""
        if all(leg.price_confidence is PriceConfidence.EXACT for leg in self.legs):
            return PriceConfidence.EXACT
        return PriceConfidence.CHECK
