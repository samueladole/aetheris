#!/usr/bin/env node
/**
 * Rendered accessibility checks for the built documentation site (T012).
 *
 * The structural gate (`html-validate`) parses static HTML, so it cannot reach
 * anything that depends on *rendered* layout. This checker drives a real
 * Chromium via Playwright to verify exactly those rules:
 *
 *   1.3.1  Info and Relationships  - no skipped heading levels
 *   1.4.3  Contrast (Minimum)      - computed contrast against AA thresholds
 *
 * Contrast is read from the browser's *computed* styles, not the authored CSS,
 * so inherited and overridden colours are judged as rendered.
 *
 * Failures exit non-zero with the offending page and rule, so the
 * documentation build stops (FR-020, VR-4).
 *
 *   node scripts/check_a11y_rendered.js site/
 *   node scripts/check_a11y_rendered.js site/ --list-rules
 *
 * ## Scope — read this before trusting SC-007
 *
 * Everything not in COVERED_RULES is UNVERIFIED, not compliant. Three WCAG
 * criteria (2.1.1 Keyboard, 2.4.3 Focus Order, 2.4.7 Focus Visible) were
 * implemented and then deliberately REMOVED, because they could not be made
 * reliable against the Material theme without theme-specific knowledge:
 * Material keeps the mobile drawer in the DOM off-canvas at desktop widths, so
 * those links are focusable but render with no visible box, and several
 * controls decline programmatic focus. A bespoke checker with that false
 * positive rate would be ignored, and an ignored gate is worse than an honest
 * gap. See docs/usage/accessibility.md.
 *
 * This checker intentionally covers fewer rules than a mature engine's ~90,
 * which is the accepted trade-off recorded in the project's implementation
 * plan.
 *
 * NOTE: browser-side checks are passed to `page.evaluate` as real functions,
 * never as strings. A string is evaluated as an expression and its uncalled
 * result is returned, so string-based checks silently report nothing.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const { chromium } = require('@playwright/test');

// WCAG thresholds - the constants that make the checks measurable.
const CONTRAST_NORMAL_AA = 4.5;    // SC 1.4.3, text < 18pt (or < 14pt bold)
const CONTRAST_LARGE_AA = 3.0;     // SC 1.4.3, text >= 18pt (or >= 14pt bold)
const LARGE_TEXT_PX = 24.0;        // >= 18pt
const LARGE_TEXT_BOLD_PX = 18.66;  // >= 14pt bold
const CONTRAST_TOLERANCE = 0.02;   // float rounding, not a real violation

/** WCAG success criteria this checker verifies. */
const COVERED_RULES = {
  '1.3.1': 'Info and Relationships - no skipped heading levels',
  '1.4.3': 'Contrast (Minimum) - computed contrast against AA thresholds',
};

/** WCAG criteria deliberately NOT verified, with the reason. */
const UNVERIFIED_RULES = {
  '2.1.1': 'Keyboard - not reliably verifiable against this theme',
  '2.4.3': 'Focus Order - not reliably verifiable against this theme',
  '2.4.7': 'Focus Visible - not reliably verifiable against this theme',
  '1.3.5': 'Identify Input Purpose - theme defect (autocomplete on checkbox)',
  '3.3.2': 'Labels or Instructions - theme defect (search form submit)',
};

const CONTRAST_THRESHOLDS = {
  normal: CONTRAST_NORMAL_AA,
  large: CONTRAST_LARGE_AA,
  largeTextPx: LARGE_TEXT_PX,
  largeTextBoldPx: LARGE_TEXT_BOLD_PX,
  tolerance: CONTRAST_TOLERANCE,
};

/** WCAG 1.3.1 - collect every heading with its level. */
function collectHeadings() {
  return Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6')).map((h) => ({
    level: Number(h.tagName[1]),
    text: (h.textContent || '').trim().slice(0, 40),
  }));
}

/**
 * WCAG 1.4.3 - compute contrast for every visible text node, entirely inside
 * the browser so each element is judged against its own composited background.
 */
function checkContrast(thresholds) {
  // `div` is included deliberately: the test fixture's low-contrast text lives
  // in a plain div, and omitting it would let that defect pass silently.
  const SELECTOR
    = 'p, li, td, th, span, a, h1, h2, h3, h4, h5, h6, code, blockquote, dd, dt, div';

  const channelLuminance = (c) => {
    const cs = c / 255.0;
    return cs <= 0.04045 ? cs / 12.92 : Math.pow((cs + 0.055) / 1.055, 2.4);
  };
  const relativeLuminance = ([r, g, b]) =>
    0.2126 * channelLuminance(r)
    + 0.7152 * channelLuminance(g)
    + 0.0722 * channelLuminance(b);
  const contrastRatio = (fg, bg) => {
    const l1 = relativeLuminance(fg);
    const l2 = relativeLuminance(bg);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  };
  const parseRgb = (value) => {
    const m = /rgba?\(([^)]+)\)/.exec(value || '');
    if (!m) return null;
    const parts = m[1].split(',').map((s) => parseFloat(s.trim()));
    if (parts.length < 3 || parts.slice(0, 3).some((n) => Number.isNaN(n))) return null;
    const rgb = parts.slice(0, 3);
    if (rgb.some((c) => c < 0 || c > 255)) return null;
    return rgb;
  };

  // Composite background layers upward until an opaque colour is reached.
  const effectiveBackground = (el) => {
    let node = el;
    const layers = [];
    while (node && node.nodeType === 1) {
      const style = getComputedStyle(node);
      const m = style.backgroundColor.match(/rgba?\(([^)]+)\)/);
      if (m) {
        const parts = m[1].split(',').map((s) => parseFloat(s.trim()));
        const [r, g, b] = parts;
        const a = parts.length > 3 ? parts[3] : 1;
        if (a > 0) {
          layers.push({ r, g, b, a });
          if (a >= 1) break;
        }
      }
      node = node.parentElement;
    }
    if (layers.length === 0) return { rgb: [255, 255, 255], source: 'default white' };
    let base = layers[layers.length - 1];
    for (let i = layers.length - 2; i >= 0; i--) {
      const top = layers[i];
      base = {
        r: top.r * top.a + base.r * (1 - top.a),
        g: top.g * top.a + base.g * (1 - top.a),
        b: top.b * top.a + base.b * (1 - top.a),
      };
    }
    return {
      rgb: [Math.round(base.r), Math.round(base.g), Math.round(base.b)],
      source: `computed from ${layers.length} layer(s)`,
    };
  };

  const violations = [];
  const seen = new Set();

  for (const el of Array.from(document.querySelectorAll(SELECTOR))) {
    const text = (el.textContent || '').trim();
    if (!text) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    if (el.getClientRects().length === 0) continue;

    const fg = parseRgb(style.color);
    if (!fg) continue;

    const { rgb: bg, source } = effectiveBackground(el);
    const ratio = contrastRatio(fg, bg);
    const fontSize = parseFloat(style.fontSize);
    const weight = Number(style.fontWeight) || 400;
    const bold = weight >= 700;
    const large =
      fontSize >= thresholds.largeTextPx
      || (bold && fontSize >= thresholds.largeTextBoldPx);
    const required = large ? thresholds.large : thresholds.normal;

    if (ratio < required - thresholds.tolerance) {
      // One report per distinct (colour, background, threshold) triple keeps
      // the output readable without hiding a genuinely different failure.
      const key = [style.color, source, String(required)].join('|');
      if (seen.has(key)) continue;
      seen.add(key);
      violations.push({
        text: text.slice(0, 40),
        ratio: Number(ratio.toFixed(2)),
        required,
        fontSize: Math.round(fontSize),
        bold,
        background: source,
      });
    }
  }
  return violations;
}

class Reporter {
  constructor() {
    this.violations = [];
  }

  add(page, rule, wcag, message) {
    this.violations.push({ page, rule, wcag, message });
  }

  get count() {
    return this.violations.length;
  }
}

async function checkPage(browser, file, root, reporter) {
  const relative = path.relative(root, file).split(path.sep).join('/');
  const page = await browser.newPage();

  try {
    await page.goto(`file://${path.resolve(file)}`, {
      waitUntil: 'load',
      timeout: 15000,
    });

    // --- WCAG 1.3.1: heading levels ------------------------------------
    const headings = await page.evaluate(collectHeadings);
    let previous = 0;
    for (const h of headings) {
      if (previous && h.level > previous + 1) {
        reporter.add(
          relative,
          'heading-order',
          '1.3.1',
          `heading level jumps h${previous} -> h${h.level} ('${h.text}')`,
        );
      }
      previous = h.level;
    }

    // --- WCAG 1.4.3: computed contrast ---------------------------------
    const contrast = await page.evaluate(checkContrast, CONTRAST_THRESHOLDS);
    for (const v of contrast) {
      reporter.add(
        relative,
        'contrast',
        '1.4.3',
        `ratio ${v.ratio} < ${v.required} required `
        + `('${v.text}', ${v.fontSize}px${v.bold ? ', bold' : ''}, bg ${v.background})`,
      );
    }
  } catch (err) {
    // A page that fails to load or throws is a FAILURE, never a pass.
    reporter.add(relative, 'error', '-', `page check failed: ${err.message}`);
  } finally {
    await page.close();
  }
}

async function main() {
  const argv = process.argv.slice(2);

  if (argv.includes('--list-rules')) {
    console.log('Covered WCAG 2.1 rules (everything else is UNVERIFIED):');
    for (const [sc, description] of Object.entries(COVERED_RULES)) {
      console.log(`  ${sc}  ${description}`);
    }
    console.log('\nDeliberately NOT verified:');
    for (const [sc, reason] of Object.entries(UNVERIFIED_RULES)) {
      console.log(`  ${sc}  ${reason}`);
    }
    return 0;
  }

  const site = argv.find((a) => !a.startsWith('--'));
  if (!site) {
    console.error('usage: check_a11y_rendered.js <site-dir> [--list-rules]');
    return 2;
  }
  if (!fs.existsSync(site) || !fs.statSync(site).isDirectory()) {
    console.error(`error: ${site} is not a directory`);
    return 2;
  }

  const pages = [];
  (function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith('.html')) pages.push(full);
    }
  })(site);
  pages.sort();

  if (pages.length === 0) {
    console.error(`error: no HTML pages under ${site}`);
    return 2;
  }

  const reporter = new Reporter();
  const browser = await chromium.launch();

  try {
    for (const file of pages) {
      await checkPage(browser, file, site, reporter);
    }
  } finally {
    await browser.close();
  }

  for (const v of reporter.violations) {
    console.log(`${v.page}: [${v.wcag}] ${v.message}`);
  }
  console.log(
    `rendered a11y: checked ${pages.length} pages, ${reporter.count} violation(s)`,
  );
  return reporter.count ? 1 : 0;
}

main().then((code) => process.exit(code));
