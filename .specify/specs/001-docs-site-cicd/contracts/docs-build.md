# Contract: Documentation Build Command

**Module**: `src/cli/main.py` (extended) · **Status**: to be implemented

The feature exposes one command surface to contributors: a documented way to build and
preview the site locally. This is the contract that command must honour.

---

## Command

```bash
aetheris docs build            # build the site, run all gates
aetheris docs serve            # build then serve locally for preview
aetheris docs open             # print the local preview URL
```

**Prerequisites**: the documentation extra installed
(`pip install -e ".[docs]"`). No provider credentials, no network access, no external
data source — the build reads only the repository's own source and Markdown.

## Gates, in order

Execution stops at the first failing gate; a partial build is never published.

| # | Gate | Command | Fails when |
|---|---|---|---|
| 1 | Docstring coverage | `ruff check --select D100,D101,D102,D103,D104,D105,D106,D107 src` | Any public entity lacks a docstring (VR-1, FR-006) |
| 2 | Secret scan | pattern scan over built `site/` output | Any credential-shaped string is found (VR-5, FR-017) |
| 3 | Site build | `mkdocs build --strict` | Any warning or error, including an unresolved nav entry |
| 4a | Accessibility — structural | `html-validate site/**/*.html` | Any rule violation (VR-4) |
| 4b | Accessibility — rendered | `node scripts/check_a11y_rendered.js site/` | Any contrast or focus violation (VR-4, VR-8) |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | All gates passed; site built into `site/` |
| `1` | Runtime failure |
| `2` | Usage error |
| `3` | A gate failed — the message names the offending file, page, or rule |

## Guarantees

- **Deterministic**: the same source produces the same output. No timestamps or
  environment values are embedded in the generated reference.
- **No network**: `aetheris docs build` completes with no internet access. This is what
  keeps the build hermetic and the accessibility gate fail-closed.
- **Idempotent**: running twice produces the same `site/` contents; the output directory is
  fully replaced, never merged.
- **Read-only over `src/`**: the build never modifies source. It parses and renders only.

## Local preview

```bash
aetheris docs serve    # http://127.0.0.1:8000, rebuilds on change
```

The preview binds to localhost only — it is a development convenience, not the published
site, and must never be exposed as a network service (Principle II / clarification #1).
