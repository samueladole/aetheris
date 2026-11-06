# Aetheris

Financial Intelligence Knowledge Graph & Sentiment Analysis Engine.

Aetheris is an automated, modular, fault-tolerant pipeline that ingests
macroeconomic data, asset prices, and financial news; scores unstructured text
with open-source financial NLP models; and structures everything into a
dynamic, queryable Knowledge Graph — all under a strict open-source-only
constraint.

## Status

The pipeline runs end to end. A scheduled `aetheris run` ingests a watchlist of
assets and macro series from FRED, Tiingo, and Finnhub, scores the news,
aligns everything as of one point in time with no lookahead, upserts it into a
DuckDB knowledge graph, and evaluates the result, as a durable workflow that
resumes from the step that failed. The [roadmap](roadmap.md) lists what is done,
the known gaps, and the milestones planned next.

## Where to go next

- **[Installation](installation.md)** — environment setup and first run
- **[Architecture](architecture.md)** — how the pipeline, layers, and guarantees work
- **[Codebase guide](codebase.md)** — what lives where, tests, scripts, conventions
- **[Roadmap](roadmap.md)** — milestones achieved and planned
- **[CLI reference](usage/cli.md)** — every command, flag, and exit code
- **[API reference](reference/)** — the public Python surface, generated from source
- **[Validation record](validation.md)** — gate results and live pipeline runs
- **[Scale measurements](scale.md)** — what the planned scale would cost
- **[Accessibility](usage/accessibility.md)** — what these docs are verified against
