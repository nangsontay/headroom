#!/usr/bin/env node
'use strict';
/**
 * Tests for pipeline-telemetry-report aggregation/rendering (pure functions).
 * Run: node --test claude/scripts/pipeline-telemetry-report.test.cjs
 */

const { test } = require('node:test');
const assert = require('node:assert');
const { parseEntries, aggregate, renderReport } = require('./pipeline-telemetry-report.cjs');

const SAMPLE = [
  '{"ts":"2026-06-17T01:00:00.000Z","hook":"plan-derive-guard","status":"warn","dur":5}',
  '{"ts":"2026-06-17T01:01:00.000Z","hook":"plan-derive-guard","status":"skip","dur":3}',
  'garbage-not-json',
  '{"ts":"2026-06-17T01:02:00.000Z","hook":"session-init","status":"ok","dur":40}',
  '{"ts":"2026-06-17T01:03:00.000Z","hook":"session-init","status":"crash","dur":2}',
  '',
].join('\n');

test('parseEntries skips blank and malformed lines', () => {
  const entries = parseEntries(SAMPLE);
  assert.strictEqual(entries.length, 4);
});

test('aggregate computes per-hook status mix and durations', () => {
  const agg = aggregate(parseEntries(SAMPLE));
  assert.strictEqual(agg.total, 4);
  assert.strictEqual(agg.hooks['plan-derive-guard'].count, 2);
  assert.strictEqual(agg.hooks['plan-derive-guard'].warn, 1);
  assert.strictEqual(agg.hooks['plan-derive-guard'].skip, 1);
  assert.strictEqual(agg.hooks['session-init'].crash, 1);
  assert.strictEqual(agg.hooks['session-init'].maxDur, 40);
  assert.strictEqual(agg.firstTs, '2026-06-17T01:00:00.000Z');
  assert.strictEqual(agg.lastTs, '2026-06-17T01:03:00.000Z');
});

test('renderReport flags crashes and handles empty input', () => {
  const report = renderReport(aggregate(parseEntries(SAMPLE)));
  assert.ok(report.includes('**Crashes detected in:** session-init'));
  assert.ok(report.includes('| plan-derive-guard | 2 |'));

  const empty = renderReport(aggregate([]));
  assert.ok(empty.includes('No telemetry yet'));
});
