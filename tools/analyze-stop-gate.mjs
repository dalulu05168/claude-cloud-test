#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const analyzer = fileURLToPath(new URL('../.claude/skills/westcott-stop-gate/scripts/analyze-stop-gate.mjs', import.meta.url));
const child = spawn(process.execPath, [analyzer, ...process.argv.slice(2)], {
  stdio: 'inherit',
  env: process.env,
});
child.on('error', (error) => {
  console.error(`[stop-gate analysis] ${error.message}`);
  process.exitCode = 1;
});
child.on('exit', (code, signal) => {
  process.exitCode = code ?? (signal ? 1 : 0);
});
