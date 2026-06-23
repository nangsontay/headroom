#!/usr/bin/env node
'use strict';
/**
 * Derive plan.md + phase-XX-*.md from <plan-dir>/decisions.log.jsonl.
 *
 * Deterministic: the same log always produces byte-identical files. Generated files
 * carry a banner and MUST NOT be hand-edited — append to the log and re-derive instead
 * (the plan-derive-guard PostToolUse hook warns if you forget).
 *
 * Note: derive overwrites the current state's files; it does not delete stale phase
 * files left behind if a phase title (hence filename) changes. The log is canonical.
 *
 * Usage: node .claude/scripts/plan-derive.cjs <plan-dir>
 */
const fs = require('fs');
const path = require('path');
const {
  GENERATED_MARKER,
  parseLog,
  buildState,
  sortedPhases,
  renderPlanMd,
  renderPhaseMd,
  phaseFileName,
} = require('./lib/plan-engine.cjs');

function main(argv) {
  const dir = argv[0];
  if (!dir) {
    console.error('Usage: plan-derive.cjs <plan-dir>');
    return 1;
  }
  const logPath = path.join(dir, 'decisions.log.jsonl');
  if (!fs.existsSync(logPath)) {
    console.error(`Error: no decisions.log.jsonl in ${dir}`);
    return 1;
  }
  let state;
  try {
    state = buildState(parseLog(fs.readFileSync(logPath, 'utf8')));
  } catch (e) {
    console.error(`Error: ${e.message}`);
    return 1;
  }
  const written = [];
  fs.writeFileSync(path.join(dir, 'plan.md'), renderPlanMd(state) + '\n');
  written.push(path.join(dir, 'plan.md'));
  for (const p of sortedPhases(state)) {
    const fp = path.join(dir, phaseFileName(p));
    fs.writeFileSync(fp, renderPhaseMd(state, p) + '\n');
    written.push(fp);
  }

  // Prune stale generated phase files (e.g. a phase title changed → filename changed).
  // Only deletes files carrying OUR generated banner — never user-authored files.
  const writtenSet = new Set(written.map((f) => path.resolve(f)));
  const pruned = [];
  for (const name of fs.readdirSync(dir)) {
    if (!/^phase-\d+.*\.md$/i.test(name)) continue;
    const fp = path.resolve(dir, name);
    if (writtenSet.has(fp)) continue;
    try {
      if (fs.readFileSync(fp, 'utf8').slice(0, 400).includes(GENERATED_MARKER)) {
        fs.unlinkSync(fp);
        pruned.push(path.join(dir, name));
      }
    } catch {
      /* ignore unreadable entries */
    }
  }

  console.log(`Derived ${written.length} file(s) from ${path.relative(process.cwd(), logPath)}:`);
  for (const f of written) console.log(`  ${path.relative(process.cwd(), f)}`);
  if (pruned.length) {
    console.log(`Pruned ${pruned.length} stale generated file(s):`);
    for (const f of pruned) console.log(`  ${path.relative(process.cwd(), f)}`);
  }
  return 0;
}

if (require.main === module) process.exit(main(process.argv.slice(2)));
module.exports = { main };
