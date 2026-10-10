# Accessibility

These docs are verified against **WCAG 2.1 Level AA** by two automated gates that
run on every documentation build and both fail closed.

## What is verified

| Gate | Tool | License | Covers |
|---|---|---|---|
| Structural | `html-validate` | MIT | alt text, labels, ARIA misuse, invalid nesting, duplicate ids/attrs |
| Rendered | bespoke Playwright checker | Apache-2.0 | computed colour contrast, keyboard focus order, focus visibility |

If either gate fails, the documentation build fails and the site does not
publish.

## How to run them

```bash
npm install
npx html-validate "site/**/*.html" --formatter stylish
python scripts/check_a11y_rendered.py site/
```

## Covered rules

The exact WCAG rule ids the rendered checker verifies are listed in the
docstring of `scripts/check_a11y_rendered.py`.

> **Read that list before assuming compliance.** Rules outside it are
> *unverified*, not *compliant*. A bespoke checker intentionally covers fewer
> rules than a mature engine's ~90; the decision to accept that trade-off is
> recorded in the project's implementation plan.

Both gates are hermetically tested against a deliberately inaccessible fixture
(`tests/fixtures/docs/a11y-bad.html`) that **must** fail every gate — a checker
that reports success when it is broken is worse than no checker.

## Reporting an issue

If you find an accessibility barrier not caught by these gates, open an issue
describing the page, the barrier, and the assistive technology involved.
