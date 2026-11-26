#!/usr/bin/env node

/**
 * One-command smoke test for the project-level Claude Code hooks.
 *
 * This does not call Claude Code or the real MCP server. It checks the project
 * registration and invokes both hook commands with representative JSONL input.
 */

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SETTINGS = resolve(ROOT, '.claude', 'settings.json');

function run(hook, input, env = {}) {
  const args = hook.args.map((argument) => argument.replace('${CLAUDE_PROJECT_DIR}', ROOT));
  const result = spawnSync(hook.command, args, {
    cwd: ROOT,
    input: typeof input === 'string' ? input : JSON.stringify(input),
    encoding: 'utf8',
    timeout: 15_000,
    maxBuffer: 2_000_000,
    env: { ...process.env, CLAUDE_PROJECT_DIR: ROOT, ...env },
  });
  assert.equal(result.error, undefined, result.error?.message);
  return result;
}

function parseStdout(result, label) {
  try {
    return result.stdout.trim() ? JSON.parse(result.stdout) : null;
  } catch (error) {
    throw new Error(`${label} 输出不是有效 JSON：${error.message}\n${result.stdout}`);
  }
}

function checkSettings() {
  const settings = JSON.parse(readFileSync(SETTINGS, 'utf8'));
  const stop = settings.hooks?.Stop?.flatMap((entry) => entry.hooks ?? [])
    .find((hook) => hook.args?.includes('${CLAUDE_PROJECT_DIR}/tools/stop-gate.mjs'));
  const preToolUse = settings.hooks?.PreToolUse
    ?.filter((entry) => entry.matcher === '^mcp__westcott-state__')
    .flatMap((entry) => entry.hooks ?? [])
    .find((hook) => hook.args?.includes('${CLAUDE_PROJECT_DIR}/tools/confirm-state-write.mjs'));
  for (const [event, hook] of [['Stop', stop], ['PreToolUse', preToolUse]]) {
    assert.equal(hook?.type, 'command', `settings.json 未注册 ${event} 命令 Hook`);
    assert.equal(hook.command, 'node', `${event} 必须直接运行 Node.js`);
  }
  assert.equal(preToolUse.onFailure, 'block', 'PreToolUse 启动失败或超时必须阻断写入');
  return { stop, preToolUse };
}

function checkStopHook(hook) {
  const valid = run(hook, {
    session_id: 'verify-hooks-session',
    prompt_id: 'verify-hooks-pass',
    last_assistant_message: '===== 资讯助理 =====\n\n下午好，今天先看大家的问题。',
  }, { WESTCOTT_STOP_GATE_LOG: 'off' });
  assert.equal(valid.status, 0, valid.stderr);
  assert.equal(valid.stdout.trim(), '', '有效输出不应被 Stop Hook 阻断');

  const invalidInput = {
    session_id: 'verify-hooks-session',
    prompt_id: 'verify-hooks-block',
    last_assistant_message: '===== 资讯助理 =====\n\n🇷🇴 罗马尼亚财经新闻｜央行公布政策决定\n\n央行公布了利率决定。\n\n市场将关注后续数据。',
  };
  const invalid = run(hook, invalidInput, { WESTCOTT_STOP_GATE_LOG: 'off' });
  assert.equal(invalid.status, 0, invalid.stderr);
  const decision = parseStdout(invalid, 'Stop Hook');
  assert.equal(decision?.decision, 'block', '格式错误的新闻应被 Stop Hook 阻断');
  assert.match(decision.reason, /配图/u);
  const retry = run(hook, { ...invalidInput, stop_hook_active: true }, { WESTCOTT_STOP_GATE_LOG: 'off' });
  assert.equal(retry.status, 0, retry.stderr);
  const retryDecision = parseStdout(retry, 'Stop Hook 修订');
  assert.equal(retryDecision?.continue, false, '修订后仍不合规时结束本轮');
  assert.match(retryDecision.stopReason, /配图/u);
}

function checkStateHook(hook) {
  const readOnly = run(hook, {
    tool_name: 'mcp__westcott-state__get_agenda',
    tool_input: {},
  });
  assert.equal(readOnly.status, 0, readOnly.stderr);
  assert.deepEqual(parseStdout(readOnly, 'PreToolUse 只读'), {});

  const write = run(hook, {
    tool_name: 'mcp__westcott-state__record_adoption',
    tool_input: { approval: '模型自行填写，不能代替用户确认' },
  });
  assert.equal(write.status, 0, write.stderr);
  const decision = parseStdout(write, 'PreToolUse 写入');
  assert.equal(decision?.hookSpecificOutput?.permissionDecision, 'ask');

  const malformed = run(hook, '{not-json');
  assert.equal(malformed.status, 2, '非法 JSON 必须 fail closed');
  assert.match(malformed.stderr, /invalid hook input/u);
}

try {
  const { stop, preToolUse } = checkSettings();
  checkStopHook(stop);
  checkStateHook(preToolUse);
  console.log('Hook smoke test passed: settings、Stop、PreToolUse 均正常。');
} catch (error) {
  console.error(`Hook smoke test failed: ${error.message}`);
  process.exitCode = 1;
}
