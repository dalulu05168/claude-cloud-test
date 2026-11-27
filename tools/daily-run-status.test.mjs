import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const server = fileURLToPath(new URL('./course-state.mjs', import.meta.url));
const date = '2026-11-27';
const runId = 'daily-' + date;
const name = 'get_daily_run_status';

function setup(t, customState = false) {
  const folder = mkdtempSync(join(tmpdir(), 'westcott-run-status-'));
  t.after(() => rmSync(folder, { recursive: true, force: true }));
  const state = join(folder, customState ? 'custom-state' : '.westcott-state');
  const run = join(state, 'runs', runId);

  function call(method, params) {
    const env = { ...process.env, CLAUDE_PROJECT_DIR: folder };
    if (customState) env.WESTCOTT_STATE_DIR = state;
    else delete env.WESTCOTT_STATE_DIR;
    const child = spawnSync(process.execPath, [server], {
      env,
      encoding: 'utf8',
      timeout: 5000,
      input: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) + '\n',
    });
    assert.equal(child.status, 0, child.stderr || String(child.error || 'Process failed'));
    return JSON.parse(child.stdout).result;
  }

  function fetch() {
    const result = call('tools/call', { name, arguments: { date } });
    assert.equal(result.isError, undefined, result.content?.[0]?.text);
    return JSON.parse(result.content[0].text);
  }

  function writeJson(file, value) {
    writeFileSync(join(run, file), JSON.stringify(value), 'utf8');
  }

  function writeArtifact(file, value) {
    writeFileSync(join(run, file), value, 'utf8');
    return {
      path: file, size_bytes: Buffer.byteLength(value), sha256: createHash('sha256').update(value).digest('hex'),
    };
  }

  function manifest(status, validation) {
    mkdirSync(run, { recursive: true });
    const draft = writeArtifact('draft.md', '未发布的候选正文');
    const research = writeArtifact('research.json', '{"stories":["未公开的研究材料"]}');
    const manifestRecord = {
      protocol_version: '1.0', run_id: runId, task_date: date, ruleset_version: 'test-rules',
      updated_at: '2026-11-27T08:00:00Z', status, validation: { status: validation },
      artifacts: { draft, research },
    };
    writeJson('manifest.json', manifestRecord);
    return manifestRecord;
  }

  return { call, fetch, manifest, run, state, writeJson, writeArtifact };
}

function version(record) {
  return { draft_sha256: record.artifacts.draft.sha256, research_sha256: record.artifacts.research.sha256 };
}

function review(record, status, issues) {
  return {
    schema_version: 'westcott-codex-review-v1', run_id: runId, task_date: date, ruleset_version: record.ruleset_version,
    ...version(record),
    review: { status, summary: '复核摘要', issues },
  };
}

test('discover and read a missing run without creating state', (t) => {
  const { call, fetch, state } = setup(t);
  const tool = call('tools/list').tools.find((tool) => tool.name === name);
  assert.equal(tool.annotations.readOnlyHint, true);
  const badDate = call('tools/call', { name, arguments: { date: '../private' } });
  assert.equal(badDate.isError, true);
  assert.deepEqual(fetch(), { date, run_id: runId, status: 'missing' });
  assert.equal(existsSync(state), false);
});

test('report current artifact versions, blocking review, and a version-bound Docs receipt', (t) => {
  const { fetch, manifest, writeArtifact, writeJson } = setup(t, true);
  const pending = manifest('drafted', 'pending_review');
  assert.deepEqual(fetch(), {
    date, run_id: runId, status: 'pending_review', ruleset_version: 'test-rules',
    version: version(pending), updated_at: pending.updated_at,
  });
  const ready = manifest('ready_for_docs', 'passed');
  const blocked = review(ready, 'needs_evidence', ['缺少官方公告']);
  writeJson('codex-review.json', blocked);
  assert.equal(fetch().status, 'blocked');
  assert.deepEqual(fetch().codex_review, blocked.review);

  const passed = review(ready, 'ready_for_human_review', []);
  writeJson('codex-review.json', passed);
  const doc = {
    run_id: runId, status: 'saved', document_id: 'doc-id',
    url: 'https://docs.google.com/document/d/doc-id/edit?opaque=test', content_sha256: ready.artifacts.draft.sha256,
  };
  writeJson('google-docs.json', doc);
  const result = fetch();
  assert.equal(result.status, 'ready_for_human_review');
  assert.deepEqual(result.codex_review, passed.review);
  assert.deepEqual(result.google_docs, {
    status: 'saved', document_id: 'doc-id', url: 'https://docs.google.com/document/d/doc-id/edit',
  });

  ready.artifacts.draft = writeArtifact('draft.md', '更新后的候选正文');
  writeJson('manifest.json', ready);
  assert.deepEqual(fetch(), {
    date, run_id: runId, status: 'ready_for_human_review', ruleset_version: 'test-rules',
    version: version(ready), updated_at: ready.updated_at,
  });
});

test('suppress results when artifacts differ from the manifest or recorded URL is unrelated', (t) => {
  const { fetch, manifest, writeArtifact, writeJson } = setup(t);
  const blocked = manifest('drafted', 'blocked');
  writeJson('google-docs.json', {
    run_id: runId, status: 'saved', content_sha256: blocked.artifacts.draft.sha256,
    document_id: 'doc-id', url: 'https://docs.google.com/document/d/different-id/edit',
  });
  writeJson('codex-review.json', review(blocked, 'ready_for_human_review', []));
  assert.equal(fetch().status, 'blocked');
  assert.equal(fetch().google_docs, undefined);
  writeArtifact('research.json', '已变更的研究材料');
  assert.deepEqual(fetch(), { date, run_id: runId, status: 'invalid' });
});
