import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SCRIPT = resolve(ROOT, 'tools', 'confirm-state-write.mjs');

function runHook(input) {
  return new Promise((resolveResult, reject) => {
    const child = spawn(process.execPath, [SCRIPT], { cwd: ROOT });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', (code) => {
      let parsed = null;
      try { parsed = stdout.trim() ? JSON.parse(stdout) : null; } catch (error) {
        reject(new Error(`invalid hook output: ${error.message}; stdout=${stdout}`));
        return;
      }
      resolveResult({ code, stdout, stderr, parsed });
    });
    child.stdin.end(JSON.stringify(input));
  });
}

test('read-only MCP tool passes without a confirmation prompt', async () => {
  const result = await runHook({ tool_name: 'mcp__westcott-state__get_agenda', tool_input: {} });
  assert.equal(result.code, 0);
  assert.deepEqual(result.parsed, {});
});

test('every persistent write asks for current user confirmation', async () => {
  const tools = [
    'save_agenda', 'save_draft', 'record_adoption', 'complete_lesson',
    'save_scene_checkpoint', 'save_handoff', 'save_fact_check',
    'save_member_profile', 'save_member_activity', 'import_mobile_handoff',
    'build_lesson_kit',
  ];
  for (const tool of tools) {
    const result = await runHook({
      tool_name: `mcp__westcott-state__${tool}`,
      tool_input: { approval: '模型自行填写的文本' },
    });
    assert.equal(result.code, 0, tool);
    assert.equal(result.parsed.hookSpecificOutput.permissionDecision, 'ask', tool);
    assert.match(result.parsed.hookSpecificOutput.permissionDecisionReason, new RegExp(tool));
  }
});

test('malformed input fails closed', async () => {
  const result = await new Promise((resolveResult, reject) => {
    const child = spawn(process.execPath, [SCRIPT], { cwd: ROOT });
    let stderr = '';
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', (code) => resolveResult({ code, stderr }));
    child.stdin.end('{not-json');
  });
  assert.equal(result.code, 2);
  assert.match(result.stderr, /invalid hook input/u);
});

test('input without a tool name fails closed', async () => {
  const result = await runHook({});
  assert.equal(result.code, 2);
  assert.match(result.stderr, /missing tool_name/u);
});

test('a full-size mobile handoff can still reach the confirmation prompt', async () => {
  const result = await runHook({
    tool_name: 'mcp__westcott-state__import_mobile_handoff',
    tool_input: { document_text: '课'.repeat(500_000) },
  });
  assert.equal(result.code, 0, result.stderr);
  assert.equal(result.parsed.hookSpecificOutput.permissionDecision, 'ask');
});
