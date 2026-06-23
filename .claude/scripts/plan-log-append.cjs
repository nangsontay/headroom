#!/usr/bin/env node
'use strict';
/**
 * Append one entry to <plan-dir>/decisions.log.jsonl (the single source of truth
 * for a plan). Assigns a stable id (D{n}, plus AD-{m} for decisions) and prints it.
 * After appending, run plan-derive.cjs to regenerate plan.md + phase files.
 *
 * Usage:
 *   node .claude/scripts/plan-log-append.cjs <plan-dir> '<json-entry>'
 *
 * Entry kinds (see claude/schemas/decision-log.schema.json):
 *   {"kind":"meta","title":"...","strategy":"...","slug":"..."}
 *   {"kind":"phase","phase":1,"title":"...","status":"pending","priority":"P1","tranche":"A","deps":[],"overview":"..."}
 *   {"kind":"decision","phase":1,"title":"...","body":"...","supersedes":"AD-2"}
 *   {"kind":"task","phase":1,"text":"..."}
 */
const fs = require('fs');
const path = require('path');
const { validateEntry, parseLog, nextIds } = require('./lib/plan-engine.cjs');

function main(argv) {
  const dir = argv[0];
  const json = argv[1];
  if (!dir || !json) {
    console.error("Usage: plan-log-append.cjs <plan-dir> '<json-entry>'");
    return 1;
  }
  let entry;
  try {
    entry = JSON.parse(json);
  } catch {
    console.error('Error: <json-entry> is not valid JSON');
    return 1;
  }
  const v = validateEntry(entry);
  if (!v.ok) {
    console.error(`Error: ${v.error}`);
    return 1;
  }
  fs.mkdirSync(dir, { recursive: true });
  const logPath = path.join(dir, 'decisions.log.jsonl');
  const existing = fs.existsSync(logPath) ? parseLog(fs.readFileSync(logPath, 'utf8')) : [];
  const ids = nextIds(existing, entry);
  // ids spread LAST so the auto-assigned id/ad always win — a caller cannot
  // inject a chosen id/ad that would collide with or shadow the stable sequence.
  const record = { ...entry, ...ids };
  fs.appendFileSync(logPath, JSON.stringify(record) + '\n');
  console.log(ids.ad ? `${ids.id} (${ids.ad})` : ids.id);
  return 0;
}

if (require.main === module) process.exit(main(process.argv.slice(2)));
module.exports = { main };
