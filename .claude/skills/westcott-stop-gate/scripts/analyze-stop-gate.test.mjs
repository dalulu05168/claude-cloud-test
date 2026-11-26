import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after } from 'node:test';
import { analyzeLogText } from './analyze-stop-gate.mjs';

const scriptPath = fileURLToPath(new URL('./analyze-stop-gate.mjs', import.meta.url));
const testDir = mkdtempSync(join(tmpdir(), 'westcott-stop-gate-analysis-'));
after(() => rmSync(testDir, { recursive: true, force: true }));

function row(record) {
  return JSON.stringify(record);
}

test('returns zero counts for an empty JSONL log', () => {
  const report = analyzeLogText('\n  \n');
  assert.equal(report.totals.records, 0);
  assert.equal(report.totals.blocked, 0);
  assert.equal(report.totals.block_rate, null);
  assert.deepEqual(report.top_reasons, []);
  assert.deepEqual(report.daily_trend, []);
});

test('aggregates statuses, issue frequencies, daily trend, and repeated prompt blocks', () => {
  const lines = [
    row({ timestamp: '2026-11-20T10:00:00.000Z', status: 'blocked', session_id: 's1', prompt_id: 'p1', issues: [
      { category: 'voice', type: 'chinese_cliche' },
      { category: 'news_format', type: 'image_footer' },
    ] }),
    row({ timestamp: '2026-11-20T10:01:00.000Z', status: 'blocked', session_id: 's1', prompt_id: 'p1', issues: [
      { category: 'voice', type: 'chinese_cliche' },
    ] }),
    row({ timestamp: '2026-11-21T10:00:00.000Z', status: 'passed', issue_counts: {} }),
    row({ timestamp: '2026-11-21T10:01:00.000Z', status: 'error', error_type: 'test_error' }),
    row({ timestamp: '2026-11-21T10:02:00.000Z', status: 'skipped' }),
  ].join('\n');

  const report = analyzeLogText(lines, { top: 10 });
  assert.equal(report.totals.records, 5);
  assert.equal(report.totals.blocked, 2);
  assert.equal(report.totals.passed, 1);
  assert.equal(report.totals.errors, 1);
  assert.equal(report.totals.skipped, 1);
  assert.equal(report.totals.block_rate, 0.6667);
  assert.equal(report.totals.prompts_blocked_more_than_once, 1);
  assert.equal(report.totals.max_blocks_for_one_prompt, 2);
  assert.deepEqual(report.top_reasons.map(({ code, count }) => [code, count]), [
    ['voice.chinese_cliche', 2],
    ['news_format.image_footer', 1],
  ]);
  assert.deepEqual(report.daily_trend.map(({ date, passed, blocked, error, skipped }) => [date, passed, blocked, error, skipped]), [
    ['2026-11-20', 0, 2, 0, 0],
    ['2026-11-21', 1, 0, 1, 1],
  ]);
  assert.equal(JSON.stringify(report).includes('s1'), false, 'report should not expose session identifiers');
  assert.equal(JSON.stringify(report).includes('p1'), false, 'report should not expose prompt identifiers');
});

test('filters valid records by UTC date while retaining malformed-line diagnostics', () => {
  const lines = [
    row({ timestamp: '2026-11-19T23:59:59Z', status: 'blocked', issues: [{ category: 'voice', type: 'hype' }] }),
    row({ timestamp: '2026-11-20T00:00:00Z', status: 'blocked', issue_counts: { 'voice.hype': 2 } }),
    '{broken json',
  ].join('\n');
  const report = analyzeLogText(lines, { since: '2026-11-20' });
  assert.equal(report.totals.records, 1);
  assert.equal(report.totals.blocked, 1);
  assert.equal(report.totals.malformed_lines, 1);
  assert.deepEqual(report.totals.malformed_line_numbers, [3]);
  assert.deepEqual(report.top_reasons.map(({ code, count }) => [code, count]), [['voice.hype', 2]]);
});

test('sorts equal-frequency causes alphabetically and applies the top limit', () => {
  const report = analyzeLogText([
    row({ timestamp: '2026-11-22T00:00:00Z', status: 'blocked', issues: [
      { category: 'voice', type: 'hype' },
      { category: 'news_format', type: 'image_footer' },
      { category: 'teacher_format', type: 'paragraph_count' },
    ] }),
  ].join('\n'), { top: 2 });
  assert.deepEqual(report.top_reasons.map(({ code }) => code), [
    'news_format.image_footer',
    'teacher_format.paragraph_count',
  ]);
});

test('ignores non-object JSON values as malformed records', () => {
  const report = analyzeLogText(`${row({ timestamp: '2026-11-22T00:00:00Z', status: 'passed' })}\n[]\nnull\n`);
  assert.equal(report.totals.records, 1);
  assert.equal(report.totals.malformed_lines, 2);
  assert.deepEqual(report.totals.malformed_line_numbers, [2, 3]);
});

test('CLI emits machine-readable JSON for a fixture file', () => {
  const fixture = join(testDir, 'cli.jsonl');
  writeFileSync(fixture, `${row({ timestamp: '2026-11-22T00:00:00Z', status: 'passed' })}\n`);
  const result = spawnSync(process.execPath, [scriptPath, fixture, '--json'], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  const parsed = JSON.parse(result.stdout);
  assert.equal(parsed.totals.passed, 1);
});

test('CLI reports missing files and invalid options without a stack trace', () => {
  const missing = spawnSync(process.execPath, [scriptPath, join(testDir, 'missing.jsonl')], { encoding: 'utf8' });
  assert.equal(missing.status, 1);
  assert.match(missing.stderr, /ENOENT/);
  assert.doesNotMatch(missing.stderr, /\n\s+at /);

  const invalid = spawnSync(process.execPath, [scriptPath, '--top', '0'], { encoding: 'utf8' });
  assert.equal(invalid.status, 1);
  assert.match(invalid.stderr, /--top/);

  const missingDate = spawnSync(process.execPath, [scriptPath, '--since'], { encoding: 'utf8' });
  assert.equal(missingDate.status, 1);
  assert.match(missingDate.stderr, /--since/);

  const invalidDate = spawnSync(process.execPath, [scriptPath, '--since', '2026-02-30'], { encoding: 'utf8' });
  assert.equal(invalidDate.status, 1);
  assert.match(invalidDate.stderr, /有效的/);
});
