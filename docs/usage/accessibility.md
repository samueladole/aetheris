# Accessibility

These docs are verified against **WCAG 2.1 Level AA** by two automated gates that
run on every documentation build and both fail closed.

## What is verified

<table>
  <thead>
    <tr>
      <th scope="col">Gate</th><th scope="col">Tool</th><th scope="col">Licence</th>
      <th scope="col">Covers</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Structural</td>
      <td><code>html-validate</code></td>
      <td>MIT</td>
      <td>Alt text, form labels, ARIA misuse, invalid nesting, duplicate ids/attributes, landmark structure</td>
    </tr>
    <tr>
      <td>Rendered</td>
      <td>Bespoke Playwright checker</td>
      <td>Apache-2.0</td>
      <td>Computed colour contrast, heading-level order</td>
    </tr>
  </tbody>
</table>

If either gate fails, the documentation build fails and the site does not
publish.

## What is **not** verified

> **Rules outside the lists below are unverified, not compliant.**

The rendered checker deliberately covers only what it can judge reliably. These
criteria are **not** verified:

<table>
  <thead>
    <tr><th scope="col">Criterion</th><th scope="col">Why not</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>2.1.1 Keyboard, 2.4.3 Focus Order, 2.4.7 Focus Visible</td>
      <td>Focus checks were implemented and then removed. Material keeps the mobile drawer off-canvas at desktop widths, so those links are focusable but render invisibly, and several controls decline programmatic focus. Telling those legitimate patterns from real defects needed theme-specific knowledge, which would make the checker a theme patch rather than an accessibility gate — and a gate with a high false-positive rate gets ignored.</td>
    </tr>
    <tr>
      <td>1.3.5 Identify Input Purpose</td>
      <td>Theme defect: Material places an <code>autocomplete</code> attribute on the dark-mode checkbox.</td>
    </tr>
    <tr>
      <td>3.3.2 Labels or Instructions</td>
      <td>Theme defect: Material's search form has no submit button.</td>
    </tr>
  </tbody>
</table>

The complete covered/unverified rule list is printed by:

```bash
node scripts/check_a11y_rendered.js site/ --list-rules
```

**Read that list before assuming compliance.** A bespoke checker intentionally
covers fewer rules than a mature engine's ~90, and the decision to accept that
gap — rather than amend the project constitution to permit the only complete
engine, which is MPL-2.0 licensed — is recorded in the project's implementation
plan.

## How to run the gates

```bash
npm install
npx playwright install chromium    # one-time, ~100MB

npx html-validate "site/**/*.html" --formatter stylish
node scripts/check_a11y_rendered.js site/
```

Both gates are hermetically tested against a deliberately inaccessible fixture,
`tests/fixtures/docs/a11y-bad.html`, which **must** fail every gate. A checker
that reports success when it is broken is worse than no checker — that exact
failure mode, reporting "1 of 1 passed" with exit code 0 on a page of
violations, is why one permissively-licensed alternative was rejected during
research.

## Reporting an issue

If you find an accessibility barrier not caught by these gates, open an issue
describing the page, the barrier, and the assistive technology involved.
