#!/usr/bin/env node
'use strict';
/**
 * pipeline-telemetry-report — summarize the hook telemetry that already exists.
 *
 * Reads the structured JSONL written by claude/hooks/lib/hook-logger.cjs
 * (.claude/hooks/.logs/hook-log.jsonl) and prints a per-hook breakdown:
 * counts, status mix (ok/warn/skip/crash), and avg/max duration. This is the
 * honest "is the pipeline machinery actually doing anything" baseline — it uses
 * data that is captured today (incl. the plan-derive-guard hook), with no new
 * capture infrastructure.
 *
 * Usage: node .claude/scripts/pipeline-telemetry-report.cjs [path-to-hook-log.jsonl]
 *
 * Future extension (not built — no data source yet): a lean-vs-full effort-tier
 * outcome comparison would require cook to emit a per-run tier+outcome event.
 * Add that emission first; do not ship empty telemetry.
 */
const fs = require('fs');
const path = require('path');

const DEFAULT_LOG = path.join(__dirname, '..', 'hooks', '.logs', 'hook-log.jsonl');

/** Parse JSONL telemetry text into entries (skips blank/garbage lines). Pure. */
function parseEntries(text) {
  const out = [];
  for (const line of String(text || '').split('\n')) {
    const t = line.trim();
    if (!t) continue;
    try {
      out.push(JSON.parse(t));
    } catch {
      /* skip malformed line — telemetry is best-effort */
    }
  }
  return out;
}

/** Aggregate entries into per-hook stats + totals. Pure. */
function aggregate(entries) {
  const hooks = {};
  let firstTs = '';
  let lastTs = '';
  for (const e of entries) {
    const name = e.hook || 'unknown';
    const h =
      hooks[name] ||
      (hooks[name] = { count: 0, ok: 0, warn: 0, skip: 0, crash: 0, other: 0, totalDur: 0, maxDur: 0 });
    h.count++;
    const status = ['ok', 'warn', 'skip', 'crash'].includes(e.status) ? e.status : 'other';
    h[status]++;
    const dur = Number(e.dur) || 0;
    h.totalDur += dur;
    if (dur > h.maxDur) h.maxDur = dur;
    if (e.ts) {
      if (!firstTs || e.ts < firstTs) firstTs = e.ts;
      if (!lastTs || e.ts > lastTs) lastTs = e.ts;
    }
  }
  return { total: entries.length, hooks, firstTs, lastTs };
}

/** Render the aggregate as a Markdown report. Pure. */
function renderReport(agg) {
  const lines = ['# Hook Pipeline Telemetry', ''];
  lines.push(`- Events: ${agg.total}`);
  if (agg.firstTs) lines.push(`- Window: ${agg.firstTs} → ${agg.lastTs}`);
  lines.push('', '| Hook | Count | ok | warn | skip | crash | avg ms | max ms |', '|------|-------|----|------|------|-------|--------|--------|');
  const names = Object.keys(agg.hooks).sort();
  for (const name of names) {
    const h = agg.hooks[name];
    const avg = h.count ? Math.round(h.totalDur / h.count) : 0;
    lines.push(`| ${name} | ${h.count} | ${h.ok} | ${h.warn} | ${h.skip} | ${h.crash} | ${avg} | ${h.maxDur} |`);
  }
  const crashed = names.filter((n) => agg.hooks[n].crash > 0);
  if (crashed.length) {
    lines.push('', `**Crashes detected in:** ${crashed.join(', ')} — investigate hook-log.jsonl.`);
  }
  if (agg.total === 0) lines.push('', '_No telemetry yet — run a session so hooks log activity._');
  return lines.join('\n');
}

function main(argv) {
  const logPath = argv[0] || DEFAULT_LOG;
  let text = '';
  try {
    text = fs.readFileSync(logPath, 'utf8');
  } catch {
    console.log(renderReport(aggregate([])));
    console.error(`\n(no telemetry log at ${logPath})`);
    return 0;
  }
  console.log(renderReport(aggregate(parseEntries(text))));
  return 0;
}

if (require.main === module) process.exit(main(process.argv.slice(2)));
module.exports = { parseEntries, aggregate, renderReport };
