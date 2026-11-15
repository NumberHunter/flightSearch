"""Journey: one direction of travel, made of one or more connecting legs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from flightsearch.domain.airport import Airport
from flightsearch.domain.leg import Leg, PriceConfidence
from flightsearch.domain.money import ExchangeRates, Money, has_mixed_currencies, sum_prices


@dataclass(frozen=True, slots=True)
class Journey:
    """An ordered chain of legs from an origin to a destination.

    A journey with more than one leg is a self-transfer: separate tickets, possibly on
    different airlines, with no protection if a connection is missed.

    This class only enforces what makes a journey physically coherent (legs connect at
    the same airport, in time order). Search policy, such as the minimum connection
    time or the maximum layover, belongs to the itinerary builder, not here.
    """

    legs: tuple[Leg, ...]

    def __post_init__(self) -> None:
        # Accept any sequence but store a tuple, so a frozen Journey can't be mutated
        # through a list the caller still holds. object.__setattr__ is the standard way
        # to set a field on a frozen dataclass during initialisation.
        object.__setattr__(self, "legs", tuple(self.legs))

        if not self.legs:
            raise ValueError("A journey needs at least one leg")

        for previous, following in zip(self.legs, self.legs[1:], strict=False):
            if previous.destination != following.origin:
                raise ValueError(
                    f"Legs don't connect: arrives at {previous.destination.iata}, "
                    f"next departs from {following.origin.iata}"
                )
            if following.departure <= previous.arrival:
                raise ValueError(
                    f"Connection at {previous.destination.iata} departs "
                    f"{following.departure.isoformat()}, not after arrival "
                    f"{previous.arrival.isoformat()}"
                )

    @classmethod
    def of(cls, *legs: Leg) -> Journey:
        """Convenience constructor: Journey.of(leg1, leg2)."""
        return cls(legs)

    @property
    def origin(self) -> Airport:
        return self.legs[0].origin

    @property
    def destination(self) -> Airport:
        return self.legs[-1].destination

    @property
    def departure(self) -> datetime:
        return self.legs[0].departure

    @property
    def arrival(self) -> datetime:
        return self.legs[-1].arrival

    @property
    def duration(self) -> timedelta:
        """Door-to-door time: first departure to last arrival, including layovers."""
        return self.arrival - self.departure

    @property
    def stops(self) -> int:
        return len(self.legs) - 1

    @property
    def is_self_transfer(self) -> bool:
        return self.stops > 0

    @property
    def layovers(self) -> tuple[timedelta, ...]:
        """Time on the ground at each connection, in order. Empty for a direct journey."""
        return tuple(
            following.departure - previous.arrival
            for previous, following in zip(self.legs, self.legs[1:], strict=False)
        )

    def total_price(self, rates: ExchangeRates | None = None) -> Money | None:
        """Sum of the leg prices; see `money.sum_prices` for the currency rules.

        None if any leg's price is unknown. Mixed currencies are converted to EUR.
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
