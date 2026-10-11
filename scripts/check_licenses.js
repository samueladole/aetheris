#!/usr/bin/env node
/**
 * Licence gate for the Node documentation toolchain (T031).
 *
 * The Python gate (`aetheris check-licenses`) walks installed Python
 * distributions, so it never sees `html-validate` or `@playwright/test` — both
 * of which the documentation accessibility gates depend on. A licence gate that
 * silently skips half the toolchain is not a gate.
 *
 * This walks `node_modules`, reads each package's declared licence, and exits
 * non-zero on anything outside the constitution's permitted set.
 *
 *   node scripts/check_licenses.js
 *   node scripts/check_licenses.js --verbose
 *
 * Permitted by constitution Principle I: MIT, Apache-2.0, BSD, ISC, and
 * public-domain equivalents. Everything else — including MPL-2.0, LGPL, and
 * SSPL — is rejected, and BSL-1.1 (source-available) is rejected too.
 */

'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const NODE_MODULES = path.join(ROOT, 'node_modules');

/** SPDX identifiers the constitution permits. */
const PERMITTED = new Set([
  'MIT',
  'MIT-0',
  'Apache-2.0',
  'Apache 2.0',
  'BSD-2-Clause',
  'BSD-3-Clause',
  'BSD-3-Clause-Clear',
  'ISC',
  '0BSD',
  'Unlicense',
  'CC0-1.0',
  'Public Domain',
  'Python-2.0',
  'Zlib',
  'BlueOak-1.0.0',
]);

/**
 * Licences rejected by name, with the reason. Used for actionable output
 * rather than a bare "not permitted".
 */
const REJECTED = {
  'MPL-2.0': 'MPL-2.0 weak copyleft - not permissive',
  'LGPL-2.1': 'LGPL copyleft - not permissive',
  'LGPL-3.0': 'LGPL copyleft - not permissive',
  'GPL-2.0': 'GPL copyleft - not permissive',
  'GPL-3.0': 'GPL copyleft - not permissive',
  'AGPL-3.0': 'AGPLv3 copyleft - not permissive',
  'SSPL-1.0': 'SSPL service copyleft - explicitly excluded',
  'BUSL-1.1': 'Business Source License - not open source',
  'Elastic-2.0': 'Elastic License - not open source',
  'CC-BY-NC-4.0': 'Non-commercial - not permitted',
};

/** Transitive dependencies we have accepted, with the reason (amendment A-001). */
const ACCEPTED = new Map([
  ['node-gyp', 'Build tooling used only at install time, never shipped in the site.'],
]);

function readPackageJson(dir) {
  const pkgPath = path.join(dir, 'package.json');
  if (!fs.existsSync(pkgPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
  } catch (_e) {
    return null;
  }
}

function walk(root) {
  const out = [];
  if (!fs.existsSync(root)) return out;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    if (entry.name.startsWith('.')) continue;

    const dir = path.join(root, entry.name);
    if (entry.name.startsWith('@')) {
      out.push(...walk(dir));
      continue;
    }
    const pkg = readPackageJson(dir);
    if (pkg) {
      out.push({ name: pkg.name || entry.name, version: pkg.version, licence: pkg.license, dir });
    }
    // Nested dependencies.
    out.push(...walk(path.join(dir, 'node_modules')));
  }
  return out;
}

function main() {
  const verbose = process.argv.includes('--verbose');

  if (!fs.existsSync(NODE_MODULES)) {
    console.error(`error: ${NODE_MODULES} not found - run \`npm install\` first`);
    process.exit(2);
  }

  const packages = walk(NODE_MODULES).filter((p) => !ACCEPTED.has(p.name));

  const rejected = [];
  const unknown = [];
  const permitted = [];

  for (const pkg of packages) {
    const licence = typeof pkg.licence === 'string'
      ? pkg.licence.trim()
      : (pkg.licence && pkg.licence.type) || '';

    if (PERMITTED.has(licence)) {
      permitted.push({ ...pkg, licence });
      continue;
    }
    if (REJECTED[licence]) {
      rejected.push({ ...pkg, licence, reason: REJECTED[licence] });
      continue;
    }
    if (!licence || licence === 'UNKNOWN' || licence === 'SEE LICENSE IN LICENSE') {
      unknown.push({ ...pkg, licence });
      continue;
    }
    // Not on either list: treat as rejected so an unrecognised value is
    // surfaced rather than waved through.
    rejected.push({ ...pkg, licence, reason: 'not on the permitted list' });
  }

  console.log(`Node licence gate (Principle I) - ${packages.length} package(s) scanned`);
  if (verbose) {
    for (const p of permitted) console.log(`  ok    ${p.name}@${p.version}  ${p.licence}`);
  }
  for (const p of unknown) {
    console.log(`  ?     ${p.name}@${p.version}  licence not declared`);
  }
  for (const p of rejected) {
    console.log(`  FAIL  ${p.name}@${p.version}  ${p.licence} - ${p.reason}`);
  }

  if (rejected.length > 0 || unknown.length > 0) {
    console.error(
      `\n${rejected.length} rejected, ${unknown.length} undetermined. ` +
      'Add an ACCEPTED entry with justification, or replace the dependency.',
    );
    process.exit(1);
  }

  console.log('ok - all Node dependencies are permissively licensed');
  process.exit(0);
}

main();
