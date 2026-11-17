# Ryanair fixtures

Saved, unmodified responses from Ryanair's unofficial website endpoints
(base URL `https://www.ryanair.com`). They exist so parsing is tested against
real data and so a changed endpoint shows up as a failing test.

Prices are only meaningful relative to the day they were fetched.
When re-capturing a file, update its date here.

| File | Endpoint | Fetched |
|---|---|---|
| `airports.json` | `/api/views/locate/5/airports/en/active` | 2026-11-09 |
| `cheapest_per_day_SOF_BGY_2026-12.json` | `/api/farfnd/v4/oneWayFares/SOF/BGY/cheapestPerDay?outboundMonthOfDate=2026-12-01&currency=EUR` | 2026-11-09 |
| `cheapest_per_day_VAR_BGY_2026-12.json` | `/api/farfnd/v4/oneWayFares/VAR/BGY/cheapestPerDay?outboundMonthOfDate=2026-12-01&currency=EUR` | 2026-11-09 |
| `schedule_SOF_BGY_2026-12.json` | `/api/timtbl/3/schedules/SOF/BGY/years/2026/months/12` | 2026-11-16 |
| `schedule_VAR_BGY_2026-12.json` | `/api/timtbl/3/schedules/VAR/BGY/years/2026/months/12` | 2026-11-16 |
| `routes_SOF.json` | `/api/views/locate/searchWidget/routes/en/airport/SOF` | 2026-11-16 |
| `routes_VAR.json` | `/api/views/locate/searchWidget/routes/en/airport/VAR` | 2026-11-16 |
| `fares_from_SOF_2026-12.json` | `/api/farfnd/v4/oneWayFares?departureAirportIataCode=SOF&outboundDepartureDateFrom=2026-12-01&outboundDepartureDateTo=2026-12-31&currency=EUR&market=en-gb&adultPaxCount=1` | 2026-11-16 |

**Not usable:** `/api/locate/v1/autocomplete/routes?...` returned HTTP 403 (an HTML
error page from Ryanair's CDN) on 2026-11-16. Use the routes widget instead.

## `cheapestPerDay` observations

- Shape: `{"outbound": {"fares": [...], "minFare": {...}, "maxFare": {...}}}`.
  One entry per calendar day of the month (31 for December), each with
  `day`, `departureDate`, `arrivalDate`, `price`, `soldOut`, `unavailable`.
- **Times are local airport time with no UTC offset**
  (e.g. `"2026-12-14T23:20:00"`). Departure is in the origin's time zone,
  arrival in the destination's; zones come from `airports.json`.
- Useful test case, SOF→BGY on 2026-12-14: departs 23:20 (Europe/Sofia, UTC+2),
  arrives 2026-12-15 00:25 (Europe/Rome, UTC+1). Real duration is 2h05,
  not the naive 1h05, and arrival is on the next calendar day.
- **No flight that day:** `price`, `departureDate`, `arrivalDate` are all
  `null`, `unavailable: true`, `soldOut: false`. SOF→BGY has 7 such days.
- `soldOut: true` does not appear in these captures. Treat it like an
  unavailable day (skip it) and parse defensively.
- **A route Ryanair doesn't fly is not an error.** VAR→BGY returns HTTP 200
  with all 31 days unavailable and `minFare`/`maxFare` = `null`. "No route" and
  "no flights this month" are indistinguishable from this endpoint alone.
- `price.value` is a JSON float (`17.99`). Convert with `Decimal(str(value))`.
  `valueMainUnit` / `valueFractionalUnit` are the same amount as strings;
  `currencySymbol` is display-only.
- `minFare` / `maxFare` duplicate the cheapest/most expensive day; not needed.

## `airports.json` observations

- A list of 222 active Ryanair airports. Each has `code` (IATA),
  `coordinates.latitude/longitude` (2–3 decimals, ~1 km), `timeZone`
  (IANA name, usable with `ZoneInfo`), `city.code`, and `country`
  (incl. `currency`, `schengen`).
- Only airports Ryanair currently serves: e.g. **LIN is missing**.
- **City codes don't give metro-area groups:** MXP has `city.code = MILAN`
  and `city.macCode = MIL`, but BGY has `city.code = BERGAMO` and no `macCode`.
  Airport groups (e.g. Milan = BGY + MXP + LIN) need our own definitions.
- No route information; see the routes widget below.

## Schedule (`timtbl`) observations

- Shape: `{"month": 12, "days": [{"day": 14, "flights": [...]}]}`. Each flight has
  `carrierCode` (`"FR"`), `number` (`"3401"`), `departureTime` and `arrivalTime`.
- **Every scheduled flight, not just the cheapest:** on 2026-12-14 SOF→BGY has
  FR3438 at 13:35 as well as FR3401 at 23:20, which is the only one the fare
  calendar shows. 10 of the 24 days have two flights.
- **No prices,** and no availability: a listed flight may be sold out. Legs built
  from this are `PriceConfidence.CHECK` with `price=None`.
- **Times are `HH:MM` local time with no date.** Departure is in the origin's zone,
  arrival in the destination's. An overnight flight shows as
  `"23:20"` → `"00:25"`: put the arrival on the departure date, and if that isn't
  after the departure (compared with time zones), move it to the next day.
- **Days without flights are left out** of `days`. The 24 days listed for SOF→BGY
  match the 24 days with a fare in `cheapest_per_day_SOF_BGY_2026-12.json`.
- **A route Ryanair doesn't fly** (VAR→BGY) returns HTTP 200 with `"days": []`.
- The cheapest-per-day flight can be matched to a schedule entry by departure time,
  which gives its flight number (the 14 Dec 23:20 fare is FR3401).

## Routes widget observations

- A JSON list, one entry per destination: 46 from SOF, 3 from VAR (FMM, KRK, KTW).
- Each entry has `arrivalAirport` (full airport details: `code`, `coordinates`,
  `timeZone`, `city`, `country`, …), `operator` (`"FR"`), `seasonal`, `recent`
  and `tags`.
- No entry in these captures has a `connectingAirport`. If one appears, it is
  Ryanair's own connecting service, not a direct route: skip it.
- `seasonal` is false everywhere here; `recent` (newly added route) is true for 14
  of the SOF routes. Neither affects whether a route exists.

## Fares-from-airport observations (not used yet)

- One request returns the cheapest fare to **every** destination in the date range:
  `{"fares": [...], "size": 34, "nextPage": null, ...}`. 34 of SOF's 46 routes have
  a December fare.
- Each fare has `outbound.departureAirport` / `arrivalAirport` (`iataCode`, name,
  city), `departureDate` / `arrivalDate` (local, no offset, like `cheapestPerDay`),
  `price`, `flightNumber` (`"FR4130"`) and `priceUpdated` (epoch milliseconds).
- Useful later for "anywhere" searches and for skipping routes with no flights in
  a search window before fetching their calendars.
