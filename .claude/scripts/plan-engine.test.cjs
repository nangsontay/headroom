#!/usr/bin/env node
'use strict';
/**
 * Tests for the decision-log plan engine: pure lib (determinism, supersede, ids,
 * validation), the append/derive CLIs end-to-end, and the guard hook.
 * Run: node --test claude/scripts/plan-engine.test.cjs
 */

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const lib = require('./lib/plan-engine.cjs');
const APPEND = path.join(__dirname, 'plan-log-append.cjs');
const DERIVE = path.join(__dirname, 'plan-derive.cjs');
const GUARD = path.join(__dirname, '..', 'hooks', 'plan-derive-guard.cjs');

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'plan-engine-'));
}
function run(script, args, opts = {}) {
  return spawnSync(process.execPath, [script, ...args], { encoding: 'utf8', ...opts });
}

const SAMPLE = [
  { kind: 'meta', title: 'Demo Plan', strategy: 'Ship in tranches.' },
  { kind: 'phase', phase: 1, title: 'Setup Env', status: 'pending', priority: 'P1', tranche: 'A', deps: [], overview: 'Bootstrap.' },
  { kind: 'phase', phase: 2, title: 'Build API', status: 'pending', priority: 'P2', deps: [1], overview: 'Endpoints.' },
  { kind: 'decision', ad: 'AD-1', phase: 2, title: 'Use REST', body: 'Simpler than GraphQL here.' },
  { kind: 'decision', ad: 'AD-2', phase: 2, title: 'Use GraphQL', body: 'Reversed.', supersedes: 'AD-1' },
  { kind: 'task', phase: 1, text: 'Install deps' },
  { kind: 'task', phase: 1, text: 'Configure env' },
];

test('buildState + render is deterministic (same log → identical output)', () => {
  const s1 = lib.buildState(SAMPLE);
  const s2 = lib.buildState(SAMPLE);
  assert.strictEqual(lib.renderPlanMd(s1), lib.renderPlanMd(s2));
  for (const p of lib.sortedPhases(s1)) {
    assert.strictEqual(lib.renderPhaseMd(s1, p), lib.renderPhaseMd(s2, p));
  }
});

test('supersede drops the superseded decision', () => {
  const state = lib.buildState(SAMPLE);
  const ads = state.decisions.map((d) => d.ad);
  assert.ok(!ads.includes('AD-1'), 'AD-1 should be superseded');
  assert.ok(ads.includes('AD-2'), 'AD-2 should remain');
});

test('generated files carry the banner', () => {
  const state = lib.buildState(SAMPLE);
  assert.ok(lib.renderPlanMd(state).startsWith(lib.GENERATED_MARKER));
  const phase1 = lib.sortedPhases(state)[0];
  assert.ok(lib.renderPhaseMd(state, phase1).includes(lib.GENERATED_MARKER));
});

test('nextIds assigns sequential ids and AD numbers', () => {
  assert.deepStrictEqual(lib.nextIds([], { kind: 'meta' }), { id: 'D1' });
  const existing = [{ kind: 'meta' }, { kind: 'decision' }];
  assert.deepStrictEqual(lib.nextIds(existing, { kind: 'decision' }), { id: 'D3', ad: 'AD-2' });
});

test('validateEntry rejects bad entries', () => {
  assert.ok(!lib.validateEntry({ kind: 'nope' }).ok);
  assert.ok(!lib.validateEntry({ kind: 'phase', title: 'x' }).ok, 'phase needs integer phase');
  assert.ok(!lib.validateEntry({ kind: 'task', phase: 1 }).ok, 'task needs text');
  assert.ok(lib.validateEntry({ kind: 'meta', title: 'ok' }).ok);
});

test('append CLI assigns ids and derive CLI renders deterministically', () => {
  const dir = tmpDir();
  try {
    const r1 = run(APPEND, [dir, JSON.stringify({ kind: 'meta', title: 'CLI Plan' })]);
    assert.strictEqual(r1.status, 0, r1.stderr);
    assert.strictEqual(r1.stdout.trim(), 'D1');

    const r2 = run(APPEND, [dir, JSON.stringify({ kind: 'phase', phase: 1, title: 'First Phase' })]);
    assert.strictEqual(r2.stdout.trim(), 'D2');

    const r3 = run(APPEND, [dir, JSON.stringify({ kind: 'decision', phase: 1, title: 'Pick X' })]);
    assert.strictEqual(r3.stdout.trim(), 'D3 (AD-1)');

    const d1 = run(DERIVE, [dir]);
    assert.strictEqual(d1.status, 0, d1.stderr);
    const planPath = path.join(dir, 'plan.md');
    const phasePath = path.join(dir, 'phase-01-first-phase.md');
    assert.ok(fs.existsSync(planPath));
    assert.ok(fs.existsSync(phasePath));
    const plan1 = fs.readFileSync(planPath, 'utf8');
    assert.ok(plan1.includes('# Plan: CLI Plan'));
    assert.ok(plan1.includes('AD-1'));

    // Re-derive → byte-identical
    run(DERIVE, [dir]);
    assert.strictEqual(fs.readFileSync(planPath, 'utf8'), plan1);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('append CLI rejects invalid entry with non-zero exit', () => {
  const dir = tmpDir();
  try {
    const r = run(APPEND, [dir, JSON.stringify({ kind: 'phase', title: 'no phase number' })]);
    assert.notStrictEqual(r.status, 0);
    assert.ok(!fs.existsSync(path.join(dir, 'decisions.log.jsonl')), 'no log written on rejection');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('guard hook warns on generated file, ignores plain file', () => {
  const dir = tmpDir();
  try {
    const gen = path.join(dir, 'plan.md');
    fs.writeFileSync(gen, lib.renderPlanMd(lib.buildState(SAMPLE)) + '\n');
    const plain = path.join(dir, 'notes.md');
    fs.writeFileSync(plain, '# Just notes\n');

    const warn = run(GUARD, [], { input: JSON.stringify({ tool_name: 'Edit', tool_input: { file_path: gen } }) });
    const warnOut = JSON.parse(warn.stdout);
    assert.strictEqual(warnOut.continue, true);
    assert.ok(/GENERATED from decisions\.log\.jsonl/.test(warnOut.additionalContext || ''), 'should warn');

    const ok = run(GUARD, [], { input: JSON.stringify({ tool_name: 'Edit', tool_input: { file_path: plain } }) });
    const okOut = JSON.parse(ok.stdout);
    assert.strictEqual(okOut.continue, true);
    assert.ok(!okOut.additionalContext, 'plain file should not warn');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('append CLI ignores caller-supplied id/ad (auto-assigned ids win)', () => {
  const dir = tmpDir();
  try {
    run(APPEND, [dir, JSON.stringify({ kind: 'meta', title: 'X' })]);
    const r = run(APPEND, [dir, JSON.stringify({ kind: 'decision', phase: 1, title: 'D', id: 'HACKED', ad: 'AD-999' })]);
    assert.strictEqual(r.stdout.trim(), 'D2 (AD-1)');
    const log = fs.readFileSync(path.join(dir, 'decisions.log.jsonl'), 'utf8').trim().split('\n');
    const last = JSON.parse(log[log.length - 1]);
    assert.strictEqual(last.id, 'D2', 'stored id must be auto-assigned, not caller value');
    assert.strictEqual(last.ad, 'AD-1', 'stored ad must be auto-assigned, not caller value');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('titles with pipes/newlines cannot break table or frontmatter', () => {
  const entries = [
    { kind: 'meta', title: 'M' },
    { kind: 'phase', phase: 1, title: 'Bad | title\nwith newline and "quote"' },
  ];
  const state = lib.buildState(entries);
  const planMd = lib.renderPlanMd(state);
  const tableRow = planMd.split('\n').find((l) => l.startsWith('| 1 |'));
  // Unescaped pipes are the real column separators; the title's pipe must be escaped (\|).
  assert.strictEqual((tableRow.match(/(?<!\\)\|/g) || []).length, 7, 'exactly 7 column separators (6 cells)');
  assert.ok(tableRow.includes('\\|'), 'title pipe is escaped, not a new column');
  const phaseMd = lib.renderPhaseMd(state, lib.sortedPhases(state)[0]);
  const titleLine = phaseMd.split('\n').find((l) => l.startsWith('title:'));
  assert.ok(!/\n/.test(state.phases.get(1).title), 'title sanitized to single line');
  assert.ok(titleLine.endsWith('"'), 'frontmatter title stays a single quoted scalar');
  assert.ok(titleLine.includes('\\"quote\\"'), 'embedded quotes are YAML-escaped');
});

test('derive prunes stale generated phase file when a phase title changes', () => {
  const dir = tmpDir();
  try {
    run(APPEND, [dir, JSON.stringify({ kind: 'meta', title: 'P' })]);
    run(APPEND, [dir, JSON.stringify({ kind: 'phase', phase: 1, title: 'Old Name' })]);
    run(DERIVE, [dir]);
    assert.ok(fs.existsSync(path.join(dir, 'phase-01-old-name.md')));
    run(APPEND, [dir, JSON.stringify({ kind: 'phase', phase: 1, title: 'New Name' })]);
    run(DERIVE, [dir]);
    assert.ok(fs.existsSync(path.join(dir, 'phase-01-new-name.md')), 'new file written');
    assert.ok(!fs.existsSync(path.join(dir, 'phase-01-old-name.md')), 'stale generated file pruned');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('validateEntry enforces phase bounds and status/priority enums', () => {
  assert.ok(!lib.validateEntry({ kind: 'phase', phase: 0, title: 'x' }).ok, 'phase < 1 rejected');
  assert.ok(!lib.validateEntry({ kind: 'phase', phase: 1e21, title: 'x' }).ok, 'huge phase rejected');
  assert.ok(!lib.validateEntry({ kind: 'phase', phase: 1, title: 'x', status: 'bogus' }).ok, 'bad status rejected');
  assert.ok(!lib.validateEntry({ kind: 'phase', phase: 1, title: 'x', priority: 'P9' }).ok, 'bad priority rejected');
  assert.ok(lib.validateEntry({ kind: 'phase', phase: 1, title: 'x', status: 'completed', priority: 'P1' }).ok);
});
