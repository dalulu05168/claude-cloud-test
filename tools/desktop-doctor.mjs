#!/usr/bin/env node
import { existsSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(process.env.CLAUDE_PROJECT_DIR || fileURLToPath(new URL('../', import.meta.url)));
const quiet = process.argv.includes('--quiet');
const checks = [];
const add = (id, ok, detail) => checks.push({ id, ok, detail });
const files = [
  'CLAUDE.md', '.claude/settings.json', '.mcp.json', '.claude/agents/westcott-reviewer.md',
  'tools/course-state.mjs', 'tools/stop-gate.mjs', 'tools/confirm-state-write.mjs',
];
for (const file of files) {
  const exists = existsSync(join(root, file));
  add(`file:${file}`, exists, exists ? 'present' : 'missing');
}

add('node', Number(process.versions.node.split('.')[0]) >= 18, `${process.version} (requires Node.js 18+)`);
try {
  const command = process.platform === 'win32' ? 'where.exe' : 'which';
  const cli = execFileSync(command, ['claude'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    timeout: 5_000, windowsHide: true }).trim();
  add('claude', true, cli.split(/\r?\n/)[0]);
} catch {
  add('claude', false, 'CLI not found on PATH (optional for the Claude Desktop app)');
}

try {
  const settings = JSON.parse(readFileSync(join(root, '.claude/settings.json'), 'utf8'));
  const hasHooks = ['Stop', 'PreToolUse'].every((event) => settings.hooks?.[event]?.some((entry) =>
    entry.hooks?.some((hook) => hook.type === 'command' && typeof hook.command === 'string')));
  add('hooks', hasHooks, hasHooks ? 'Stop + PreToolUse registered' : 'Stop or PreToolUse registration missing');
} catch (error) {
  add('hooks', false, `could not read settings: ${error.message}`);
}

const failed = checks.filter((check) => !check.ok);
const result = { project_dir: root, status: failed.length ? 'degraded' : 'healthy', checks };
process.exitCode = failed.some((check) => check.id !== 'claude') ? 1 : 0;
if (!quiet || process.exitCode) console.log(JSON.stringify(result, null, 2));
