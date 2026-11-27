#!/usr/bin/env node

/**
 * Production health check for the Westcott Claude Code project.
 *
 * Safe by default: it reads project files and invokes local hooks with synthetic
 * input only. It does not call Claude Code, MCP, Google Drive, or mutate
 * .westcott-state business data. Use --json for monitoring/CI integration.
 */

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { accessSync, constants, existsSync, readFileSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const JSON_MODE = process.argv.includes('--json');
const checks = [];

function record(id, passed, detail, severity = 'error') {
  checks.push({ id, passed, detail, severity: passed ? 'ok' : severity });
}

function fileExists(relativePath) {
  return existsSync(resolve(ROOT, relativePath));
}

function runNode(script, input, extraEnv = {}) {
  return spawnSync(process.execPath, [resolve(ROOT, script)], {
    cwd: ROOT,
    input: typeof input === 'string' ? input : JSON.stringify(input),
    encoding: 'utf8',
    timeout: 15_000,
    maxBuffer: 2_000_000,
    env: {
      ...process.env,
      CLAUDE_PROJECT_DIR: ROOT,
      ...extraEnv,
    },
  });
}

function parseJsonOutput(result, label) {
  assert.equal(result.error, undefined, result.error?.message);
  return result.stdout.trim() ? JSON.parse(result.stdout) : null;
}

function checkRuntime() {
  const major = Number(process.versions.node.split('.')[0]);
  record('node-version', major >= 18, `Node.js ${process.version}（要求 >= 18）`);
}

function checkFiles() {
  const required = [
    '.claude/settings.json',
    '.claude/agents/westcott-reviewer.md',
    'CLAUDE.md',
    'tools/stop-gate.mjs',
    'tools/confirm-state-write.mjs',
    'tools/whatsapp-voice.mjs',
    'tools/speech-format.mjs',
    '.mcp.json',
    'tools/course-state.mjs',
  ];
  for (const relativePath of required) {
    record(`file:${relativePath}`, fileExists(relativePath), fileExists(relativePath) ? '存在' : '缺失');
  }
}

function checkSettings() {
  try {
    const settings = JSON.parse(readFileSync(resolve(ROOT, '.claude/settings.json'), 'utf8'));
    const reviewer = readFileSync(resolve(ROOT, '.claude/agents/westcott-reviewer.md'), 'utf8');
    const usesReviewer = /^model: haiku\s*$/m.test(reviewer)
      && /^tools: Read, Glob, Grep, mcp__westcott-state__review_candidate_draft\s*$/m.test(reviewer);
    record('settings:agent', !settings.agent && usesReviewer, '当前会话直接处理，独立审稿使用只读 Haiku Agent');
    const stopOk = (settings.hooks?.Stop ?? [])
      .flatMap((entry) => entry.hooks ?? [])
      .some((hook) => hook.type === 'command' && hook.command === 'node'
        && hook.args?.includes('${CLAUDE_PROJECT_DIR}/tools/stop-gate.mjs'));
    record('settings:stop', stopOk, stopOk ? 'Stop Hook 已注册' : '未找到直接运行的 Stop Hook');

    const preEntries = settings.hooks?.PreToolUse ?? [];
    const preHook = preEntries.filter((entry) => entry.matcher === '^mcp__westcott-state__')
      .flatMap((entry) => entry.hooks ?? [])
      .find((hook) => hook.type === 'command' && hook.command === 'node'
        && hook.args?.includes('${CLAUDE_PROJECT_DIR}/tools/confirm-state-write.mjs'));
    record('settings:pretooluse', Boolean(preHook), preHook
      ? 'PreToolUse 状态写入确认 Hook 已注册'
      : '未找到 mcp__westcott-state__ 的确认 Hook');

    const failureBlocking = preHook?.onFailure === 'block';
    record('settings:onFailure', failureBlocking, failureBlocking
      ? 'PreToolUse 失败时阻断'
      : 'PreToolUse 未配置 onFailure=block');
  } catch (error) {
    record('settings:parse', false, `settings.json 无法解析：${error.message}`);
  }
}

function checkMcpConfig() {
  try {
    const config = JSON.parse(readFileSync(resolve(ROOT, '.mcp.json'), 'utf8'));
    const server = config.mcpServers?.['westcott-state'];
    const ok = server?.type === 'stdio' && server.command === 'node'
      && server.args?.includes('${CLAUDE_PROJECT_DIR:-.}/tools/course-state.mjs');
    record('settings:mcp', Boolean(ok), ok ? 'westcott-state 启动参数及路径回退已配置' : 'westcott-state 缺少正确的启动命令或路径回退');
  } catch (error) {
    record('settings:mcp', false, `.mcp.json 无法解析：${error.message}`);
  }
}

function checkSyntax() {
  for (const relativePath of [
    'tools/stop-gate.mjs',
    'tools/confirm-state-write.mjs',
    'tools/whatsapp-voice.mjs',
    'tools/speech-format.mjs',
  ]) {
    const result = spawnSync(process.execPath, ['--check', resolve(ROOT, relativePath)], {
      cwd: ROOT,
      encoding: 'utf8',
      timeout: 10_000,
    });
    record(`syntax:${relativePath}`, result.status === 0, result.status === 0
      ? '语法检查通过'
      : `${result.stderr || result.stdout || `exit ${result.status}`}`);
  }
}

function checkStopHook() {
  try {
    const pass = runNode('tools/stop-gate.mjs', {
      session_id: 'health-check',
      prompt_id: 'health-check-pass',
      last_assistant_message: '===== 资讯助理 =====\n\n下午好，今天先看大家的问题。',
    }, { WESTCOTT_STOP_GATE_LOG: 'off' });
    assert.equal(pass.status, 0, pass.stderr);
    assert.equal(pass.stdout.trim(), '');

    const blocked = runNode('tools/stop-gate.mjs', {
      session_id: 'health-check',
      prompt_id: 'health-check-block',
      last_assistant_message: '===== 资讯助理 =====\n\n🇷🇴 罗马尼亚财经新闻｜央行公布政策决定\n\n央行公布了利率决定。\n\n市场将关注后续数据。',
    }, { WESTCOTT_STOP_GATE_LOG: 'off' });
    assert.equal(blocked.status, 0, blocked.stderr);
    const decision = parseJsonOutput(blocked, 'Stop Hook');
    assert.equal(decision?.decision, 'block');
    record('hook:stop', true, 'Stop Hook 可正常放行并阻断测试输入');
  } catch (error) {
    record('hook:stop', false, `Stop Hook 检查失败：${error.message}`);
  }
}

function checkPreToolUse() {
  try {
    const readOnly = runNode('tools/confirm-state-write.mjs', {
      tool_name: 'mcp__westcott-state__get_agenda',
      tool_input: {},
    });
    assert.equal(readOnly.status, 0, readOnly.stderr);
    assert.deepEqual(parseJsonOutput(readOnly, 'PreToolUse 只读'), {});

    const write = runNode('tools/confirm-state-write.mjs', {
      tool_name: 'mcp__westcott-state__record_adoption',
      tool_input: {},
    });
    assert.equal(write.status, 0, write.stderr);
    assert.equal(parseJsonOutput(write, 'PreToolUse 写入')?.hookSpecificOutput?.permissionDecision, 'ask');

    const malformed = runNode('tools/confirm-state-write.mjs', '{not-json');
    assert.equal(malformed.status, 2);
    record('hook:pretooluse', true, 'PreToolUse 可放行只读、询问写入并拒绝非法输入');
  } catch (error) {
    record('hook:pretooluse', false, `PreToolUse 检查失败：${error.message}`);
  }
}

function checkStateAccess() {
  const stateDir = resolve(ROOT, '.westcott-state');
  if (!existsSync(stateDir)) {
    record('state:directory', true, '.westcott-state 尚未创建（首次运行可正常创建）', 'warning');
    return;
  }
  try {
    const stat = statSync(stateDir);
    assert.ok(stat.isDirectory(), '.westcott-state 不是目录');
    accessSync(stateDir, constants.R_OK | constants.W_OK | constants.X_OK);
    record('state:directory', true, '.westcott-state 可读写');
  } catch (error) {
    record('state:directory', false, `.westcott-state 不可正常访问：${error.message}`);
  }
}

function checkTempAccess() {
  try {
    accessSync(tmpdir(), constants.R_OK | constants.W_OK | constants.X_OK);
    record('runtime:temp', true, `临时目录可用：${tmpdir()}`);
  } catch (error) {
    record('runtime:temp', false, `临时目录不可用：${error.message}`);
  }
}

checkRuntime();
checkFiles();
checkSettings();
checkMcpConfig();
checkSyntax();
checkStopHook();
checkPreToolUse();
checkStateAccess();
checkTempAccess();

const failed = checks.filter((entry) => !entry.passed && entry.severity === 'error');
const warnings = checks.filter((entry) => !entry.passed && entry.severity === 'warning');
const result = {
  status: failed.length ? 'unhealthy' : warnings.length ? 'degraded' : 'healthy',
  project_dir: ROOT,
  checked_at: new Date().toISOString(),
  summary: { total: checks.length, passed: checks.filter((entry) => entry.passed).length, failed: failed.length, warnings: warnings.length },
  checks,
};

if (JSON_MODE) {
  console.log(JSON.stringify(result, null, 2));
} else {
  for (const check of checks) {
    console.log(`${check.passed ? 'PASS' : check.severity === 'warning' ? 'WARN' : 'FAIL'} ${check.id}: ${check.detail}`);
  }
  console.log(`Health status: ${result.status} (${result.summary.passed}/${result.summary.total} checks passed)`);
}

process.exitCode = failed.length ? 1 : 0;
