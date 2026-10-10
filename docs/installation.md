# Installation & First Run

## Requirements

- Python 3.11 or newer
- No GPU required
- No paid API keys required for a dry run

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## First run (no credentials needed)

```bash
aetheris run --trigger schedule --dry-run
```

A dry run validates configuration and quotas without calling any provider, so it
needs no API keys and consumes no free-tier quota.

## Live runs

Set credentials in the environment — never commit them:

```bash
export FRED_API_KEY="..."
export FINNHUB_API_KEY="..."
export FMP_API_KEY="..."
export TIINGO_API_KEY="..."
export ALPHA_VANTAGE_API_KEY="..."

aetheris run --trigger schedule
```

## Verify your installation

```bash
aetheris check-licenses --lenient   # constitution Principle I gate
aetheris status                     # provider posture and trigger surface
aetheris version
```

## Optional: documentation tooling

To build these docs locally:

```bash
pip install -e ".[docs]"
npm install
aetheris docs serve                 # http://127.0.0.1:8000
```
