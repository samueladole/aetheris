# Cassette Recording

Invariant A6 requires that no test makes a live provider call, so CI never
consumes free-tier quota. Two mechanisms achieve that here:

- **`tests/integration/`** uses `FakeHttpClient` — a scripted HTTP client that
  returns queued responses. This is what currently satisfies A6, and it covers
  the fallback, caching, and immutability paths.
- **VCR cassettes** (this directory) replay *real* recorded provider responses
  byte-for-byte. They are the stronger check because they catch changes in
  provider response *shape*, which a hand-written fake cannot.

## Status: partially recorded

6 of 11 cassettes recorded:

| Provider | Endpoint | Status |
|---|---|---|
| FRED | series/observations | recorded |
| Alpha Vantage | GLOBAL_QUOTE | recorded |
| Finnhub | quote | recorded |
| Finnhub | stock/profile2 | recorded |
| Finnhub | /news | recorded |
| FMP | historical-price-full | recorded (403 body) |
| FMP | quote | not recorded — 403 on Basic tier |
| FMP | fmp/articles | not recorded — 403 on Basic tier |
| Tiingo | tiingo/daily | not recorded — 403 on Basic tier |
| Tiingo | iex | not recorded — path fix invalidated it |
| Tiingo | tiingo/news | not recorded — 403 on Basic tier |

FMP's Basic tier restricts `/quote` and `/articles`, and Tiingo's free tier
rejects several endpoints with 403. Those are plan limitations, not code defects.

Two adapter bugs were found *by* recording, which is the clearest argument for
doing it: Tiingo's base URL produced `/tiingo/tiingo/daily` (a 404), and the ticker
belonged in the path rather than the query string. Both are fixed; neither
cassette was re-recorded because the remaining quota was reserved.

## Credential leak — filtered at record time

The first recording pass captured **real API keys in the request URIs**: Finnhub
and FMP pass keys as query parameters, and Tiingo uses an `Authorization` header.
Those cassettes were scrubbed, and the recorder now passes
`filter_query_parameters` and `filter_headers` to `vcr.use_cassette`, so keys never
reach disk.

`tests/contract/test_cassettes.py` fails if a cassette ever contains a live
credential again. **Re-check this if the recording code changes.**

## Recording

To record, run:

```bash
python scripts/record_cassettes.py    # writes one .yaml per provider/endpoint
```

Each pass consumes a tiny amount of quota — roughly 11 requests, with Tiingo's
50 requests per hour the tightest binding constraint. Record once, not per test
run.

## Quota cost of one full recording pass

| Provider | Calls | Quota impact |
|---|---|---|
| FRED | 1 | 1 of unlimited (120/min) |
| Alpha Vantage | 1 | 1 of 25/day |
| Finnhub | 3 | 3 of 60/min |
| FMP | 3 | 3 of 250/day |
| Tiingo | 3 | 3 of 50/hour |

## Safety

Cassettes may contain real API responses, which may include provider-specific
identifiers. They are safe to commit **only if** the responses contain no
secrets. Recording filters credentials, and the test suite asserts it — but
review a newly recorded cassette before committing anyway.
