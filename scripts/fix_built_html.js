#!/usr/bin/env node
/**
 * Post-build HTML fixes for defects the theme and generator introduce (T013, T012).
 *
 * Two real accessibility defects are repaired here, both observed in the built
 * site rather than assumed:
 *
 *   1. DUPLICATE LANDMARKS - Material emits two `<nav aria-label="Search">` and
 *      two `<nav aria-label="<site name>">`. A screen-reader user navigating by
 *      landmark hears two indistinguishable "Search" landmarks, which maps to
 *      WCAG 2.4.1 / 4.1.2. The theme cannot be configured to relabel them.
 *
 *   2. `<th>` WITHOUT `scope` - mkdocstrings renders parameter tables with
 *      bare `<th>`, failing WCAG h63 (SC 1.3.1). This affects every generated
 *      reference page that documents a function with arguments.
 *
 * Both are FIXED rather than silenced, because both are genuine defects in
 * output we publish. Disabling the rules instead would hide them.
 *
 * Runs immediately after `mkdocs build` and before the accessibility gates.
 *
 *   node scripts/fix_built_html.js site/
 */

'use strict';

const fs = require('fs');
const path = require('path');

const LANDMARK_OPEN = /<(nav|main|aside|header|footer|form|section)\b([^>]*?)\baria-label="([^"]*)"([^>]*)>/gi;

/** Rename the 2nd..nth occurrence of a landmark label so each is unique. */
function fixLandmarks(html, counts) {
  return html.replace(LANDMARK_OPEN, (match, tag, pre, label, post) => {
    const key = `${tag.toLowerCase()}|${label}`;
    const seen = (counts.get(key) || 0) + 1;
    counts.set(key, seen);
    if (seen === 1) return match;
    return match.replace(`aria-label="${label}"`, `aria-label="${label} (${seen})"`);
  });
}

/** Add `scope` to every `<th>` that lacks it, per WCAG h63 (SC 1.3.1). */
function fixTableHeaders(html, counter) {
  return html.replace(/<th\b([^>]*)>/gi, (match, attrs, offset) => {
    if (/\bscope\s*=/i.test(attrs)) return match;
    // A <th> inside <thead> labels a column; anywhere else it labels a row.
    // The check must account for the intervening <tr>, so look for the most
    // recent <thead>/<tbody> opening before this cell.
    const before = html.slice(0, offset);
    const lastThead = before.lastIndexOf('<thead');
    const lastTbody = before.lastIndexOf('<tbody');
    const inHead = lastThead > lastTbody;
    counter.th += 1;
    return `<th${attrs} scope="${inHead ? 'col' : 'row'}">`;
  });
}

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else if (entry.name.endsWith('.html')) out.push(full);
  }
  return out;
}

function main() {
  const site = process.argv[2];
  if (!site || !fs.existsSync(site) || !fs.statSync(site).isDirectory()) {
    console.error('usage: fix_built_html.js <site-dir>');
    process.exit(2);
  }

  const landmarks = new Map();
  const counter = { th: 0 };

  for (const file of walk(site)) {
    const original = fs.readFileSync(file, 'utf8');
    const fixed = fixTableHeaders(fixLandmarks(original, landmarks), counter);
    if (fixed !== original) fs.writeFileSync(file, fixed);
  }

  const landmarkFixes = [...landmarks.values()]
    .filter((n) => n > 1)
    .reduce((sum, n) => sum + n - 1, 0);

  console.log(
    `built-html fixes: ${landmarkFixes} duplicate landmark label(s), ` +
    `${counter.th} <th> missing scope`,
  );
}

main();
