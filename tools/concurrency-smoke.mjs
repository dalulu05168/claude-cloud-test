import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const server = join(root, 'tools', 'course-state.mjs');
const count = Number(process.argv[2] ?? 4);
if (!Number.isInteger(count) || count < 2 || count > 16) throw new Error('Use a connection count from 2 to 16.');

const protocol = '2025-11-25';
const request = (id, method, params = {}) => `${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`;

function run(index) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [server], {
      cwd: root,
      env: { ...process.env, CLAUDE_PROJECT_DIR: root },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    const responses = new Map();
    let errors = '';
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => { errors += chunk; });

    const timer = setTimeout(() => fail(new Error('timed out after 10 seconds')), 10_000);
    function fail(error) {
      clearTimeout(timer);
      child.kill();
      reject(new Error(`connection ${index}: ${error.message}`, { cause: error }));
    }

    child.once('error', fail);
    child.stdin.on('error', fail);
    child.once('close', (code, signal) => {
      clearTimeout(timer);
      if (code !== 0) return fail(new Error(`server exited ${code ?? signal}: ${errors.trim()}`));
      if (!responses.has(1)) return fail(new Error('initialize failed'));
      const tools = responses.get(2)?.tools;
      const ping = responses.get(3);
      if (!Array.isArray(tools) || !tools.length) return fail(new Error('tools/list failed'));
      if (!ping || Object.keys(ping).length !== 0) return fail(new Error('ping failed'));
      if (responses.size !== 3) return fail(new Error(`received ${responses.size} replies, expected 3`));
      resolve(tools.length);
    });

    const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
    lines.on('line', (line) => {
      try {
        const { jsonrpc, id, result, error: rpcError } = JSON.parse(line);
        if (jsonrpc !== '2.0' || rpcError || !result || typeof result !== 'object' || Array.isArray(result)
          || ![1, 2, 3].includes(id) || responses.has(id)) {
          throw new Error('unexpected JSON-RPC reply');
        }
        responses.set(id, result);
        if (id === 1) {
          if (result.protocolVersion !== protocol || result.serverInfo?.name !== 'westcott-state') {
            throw new Error('initialize failed');
          }
          const initialized = JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' });
          child.stdin.end(`${initialized}\n${request(2, 'tools/list')}${request(3, 'ping')}`);
        }
      } catch (error) {
        fail(error);
      }
    });

    child.stdin.write(request(1, 'initialize', {
      protocolVersion: protocol, capabilities: {}, clientInfo: { name: `smoke-${index}`, version: '1' },
    }));
  });
}

const results = await Promise.all(Array.from({ length: count }, (_, index) => run(index + 1)));
console.log(JSON.stringify({
  ok: true, connections: results.length, tools_per_connection: [...new Set(results)], writes_tested: false,
}));
