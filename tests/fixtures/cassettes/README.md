# Cassette Recording

Invariant A6 requires that no test makes a live provider call, so CI never
consumes free-tier quota. Two mechanisms achieve that here:

- **`tests/integration/`** uses `FakeHttpClient` — a scripted HTTP client that
  returns queued responses. This is what currently satisfies A6, and it covers
  the fallback, caching, and immutability paths.
- **VCR cassettes** (this directory) replay *real* recorded provider responses
  byte-for-byte. They are the stronger check because they catch changes in
  provider response *shape*, which a hand-written fake cannot.

## Status: not yet recorded

`vcrpy` is installed and wired into the dev dependencies, but **no cassettes are
recorded yet**. Recording requires live API keys for all five providers, which
are not available in this environment. Recording them is a remaining task.

To record, run:

```bash
export FRED_API_KEY=...
export ALPHA_VANTAGE_API_KEY=...
export FINNHUB_API_KEY=...
export FMP_API_KEY=...
export TIINGO_API_KEY=

python scripts/record_cassettes.py    # writes one .yaml per provider/endpoint
```

`scripts/record_cassettes.py` performs one call per provider/endpoint pair from
the spec §3.2.1 registry and writes the response under this directory. Each
recording consumes a tiny amount of the free-tier quota, so it is run manually
rather than in CI.

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
secrets — review the recorded YAML before committing, and never commit a
cassette that contains an `api_key`, `token`, or `Authorization` header value.
Recording uses placeholder test symbols (`AAPL`, `GDP`) precisely to keep the
content non-sensitive.
