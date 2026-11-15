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
- No route information; routes need a separate endpoint.
