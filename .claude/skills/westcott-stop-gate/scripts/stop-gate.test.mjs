import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { after } from 'node:test';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptPath = fileURLToPath(new URL('./stop-gate.mjs', import.meta.url));
const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
const settingsPath = join(projectDir, '.claude', 'settings.json');
const testLogDir = mkdtempSync(join(tmpdir(), 'westcott-stop-gate-test-'));
const testLogPath = join(testLogDir, 'stop-gate.jsonl');

after(() => rmSync(testLogDir, { recursive: true, force: true }));

test('project settings register the Stop hook and point to this gate script', (t) => {
  if (!existsSync(settingsPath)) {
    t.skip('no project settings file in the current working directory');
    return;
  }
  const settings = JSON.parse(readFileSync(settingsPath, 'utf8'));
  const stopHandlers = settings.hooks?.Stop ?? [];
  const commands = stopHandlers.flatMap((entry) => entry.hooks ?? [])
    .filter((hook) => hook.type === 'command')
    .flatMap((hook) => [hook.command, ...(hook.args ?? [])]);
  assert.ok(commands.some((command) => command.includes('tools/stop-gate.mjs')
    || command.includes('.claude/skills/westcott-stop-gate/scripts/stop-gate.mjs')));
});

function runHook(input, { raw = false, envOverrides = {} } = {}) {
  const inputText = raw ? input : JSON.stringify(input);
  return spawnSync(process.execPath, [scriptPath], {
    input: inputText,
    encoding: 'utf8',
    timeout: 10_000,
    maxBuffer: 2_000_000,
    env: {
      ...process.env,
      WESTCOTT_STOP_GATE_LOG: envOverrides.WESTCOTT_STOP_GATE_LOG
        ?? process.env.WESTCOTT_STOP_GATE_LOG ?? testLogPath,
      ...envOverrides,
    },
  });
}

function resultFor(message) {
  const result = runHook({ last_assistant_message: message });
  assert.equal(result.error, undefined, result.error?.message);
  return result;
}

function assertPass(message, name = 'expected to pass') {
  const result = resultFor(message);
  assert.equal(result.status, 0, `${name}: ${result.stderr || result.stdout}`);
  assert.equal(result.stdout.trim(), '', `${name}: unexpected hook output ${result.stdout}`);
}

function assertBlocked(message, expectedText, name = 'expected to be blocked') {
  const result = resultFor(message);
  assert.equal(result.status, 0, `${name}: script itself should exit normally`);
  const decision = JSON.parse(result.stdout.trim());
  assert.equal(decision.decision, 'block', name);
  assert.match(decision.reason, expectedText, name);
}

function newsBlock({
  role = '===== 资讯助理 =====',
  title = '🇷🇴 罗马尼亚财经新闻｜央行公布政策决定',
  facts = '央行公布了利率决定。',
  impact = '市场将关注后续通胀数据。',
  footer = '配图：https://cdn.example.com/event.jpg?width=1200',
} = {}) {
  return `${role}\n\n${title}\n\n${facts}\n\n${impact}${footer ? `\n\n${footer}` : ''}`;
}

test('passes when there is no final assistant message', () => {
  const result = runHook({});
  assert.equal(result.status, 0);
  assert.equal(result.stdout.trim(), '');
});

test('passes a short assistant opening without a news block', () => {
  assertPass('===== 资讯助理 =====\n\n下午好，今天先看一下大家的问题。');
});

test('does not apply dialogue sensitive-word rules to unstructured explanatory prose', () => {
  assertPass('这里讨论“家人们”这个词为什么不适合用于正式群聊。');
});

test('passes a correctly formatted news item with a direct HTTPS URL', () => {
  assertPass(newsBlock());
});

test('accepts CRLF line endings and extra blank lines', () => {
  assertPass(newsBlock().replaceAll('\n', '\r\n').replace('央行公布了利率决定。', '央行公布了利率决定。\r\n\r\n'));
});

test('blocks a news item missing the image footer', () => {
  assertBlocked(newsBlock({ footer: '' }), /配图/);
});

test('rechecks the corrected message on a Stop hook continuation', () => {
  const result = runHook({ stop_hook_active: true, last_assistant_message: newsBlock() });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), '');
});

test('ends a Stop hook continuation with a user-visible reason if validation still fails', () => {
  const result = runHook({ stop_hook_active: true, last_assistant_message: newsBlock({ footer: '' }) });
  assert.equal(result.status, 0, result.stderr);
  const decision = JSON.parse(result.stdout);
  assert.equal(decision.continue, false);
  assert.equal(decision.decision, undefined);
  assert.match(decision.stopReason, /待人工核对.*配图/su);
});

test('blocks malformed image footer labels', () => {
  assertBlocked(newsBlock({ footer: '图片：https://cdn.example.com/event.jpg' }), /配图/);
});

test('blocks non-HTTP(S), markdown-wrapped, credentialed, and malformed image URLs', async (t) => {
  const invalidUrls = [
    '配图:ftp://cdn.example.com/event.jpg',
    '配图：https://cdn.example.com/event.jpg extra-text',
    '配图：[图片](https://cdn.example.com/event.jpg)',
    '配图：https://user:pass@cdn.example.com/event.jpg',
    '配图:not-a-url',
  ];
  for (const footer of invalidUrls) {
    await t.test(footer, () => assertBlocked(newsBlock({ footer }), /配图|图片直链/));
  }
});

test('blocks a malformed or missing headline even when there are three news paragraphs', () => {
  assertBlocked(newsBlock({ title: '央行更新政策' }), /标题|国旗/);
});

test('passes a multi-paragraph assistant announcement without news markers', () => {
  const announcement = [
    '===== 资讯助理 =====',
    '',
    '今天先说明课程安排。',
    '',
    '材料会在晚些时候发出。',
    '',
    '请先保留问题，等讲师答复。',
  ].join('\n');
  assertPass(announcement);
});

test('blocks a country flag that conflicts with the country name', () => {
  assertBlocked(newsBlock({ title: '🇺🇸 罗马尼亚财经新闻｜央行公布政策决定' }), /国旗/);
});

test('blocks news with too few body paragraphs', () => {
  assertBlocked(newsBlock({ impact: '' }), /正文需 3 段/);
});

test('blocks a non-canonical assistant role label on a news block', () => {
  assertBlocked(newsBlock({ role: '===== 助理 =====' }), /角色标记|发言标记/);
});

test('checks every likely news block in the same final message', () => {
  const twoNews = `${newsBlock()}\n\n${newsBlock({
    title: '🇺🇸 美国财经新闻｜公布重要经济数据',
    footer: '',
  })}`;
  assertBlocked(twoNews, /配图/);
});

test('passes a David message with exactly two paragraphs', () => {
  assertPass('===== David先生 =====\n\n先看收盘价和成交量，图表本身不能确认交易者身份。\n\n再观察关键位能否守住，并结合后续数据验证。');
});

test('passes a David message with one paragraph', () => {
  assertPass('===== David先生 =====\n\n先看收盘价和成交量。');
});

test('blocks an existing Chinese cliché from the project voice rules', () => {
  assertBlocked('12男：家人们，我先看收盘。', /套话|家人们/);
});

test('blocks Chinese market slang, including spaces inserted between Han characters', () => {
  assertBlocked('===== David先生 =====\n\n这里是主力洗 盘，接着就能抄底。\n\n我们还是回到收盘数据验证。', /黑话|主力洗盘|抄底/);
});

test('blocks hype phrases from the project voice rules', () => {
  assertBlocked('12男：这下 to the moon! 🚀', /炒作|to the moon/);
});

test('passes normal market terms that are not on the blocked list', () => {
  assertPass('12男：我看了 BVB 的 BET 和成交量，先等收盘确认。');
});

test('does not block advisory-only voice findings such as emoji density', () => {
  assertPass('12男：我先看收盘。🙂🤔');
});

test('blocks malformed hook JSON instead of silently skipping validation', () => {
  const result = runHook('{', { raw: true });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /invalid JSON/i);
});

test('blocks oversized hook input', () => {
  const result = runHook('x'.repeat(1_000_001), { raw: true });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /exceeds/);
});

test('falls back to bundled validators when a custom directory contains invalid modules', () => {
  const brokenDir = join(testLogDir, 'broken-validators');
  mkdirSync(brokenDir, { recursive: true });
  writeFileSync(join(brokenDir, 'speech-format.mjs'), 'export const broken = ;\n');
  writeFileSync(join(brokenDir, 'whatsapp-voice.mjs'), 'export const broken = ;\n');
  const result = runHook({ last_assistant_message: '简短开场。' }, {
    envOverrides: { WESTCOTT_VALIDATORS_DIR: brokenDir },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), '');
});

test('validator exceptions write status:error, exit 2, and expose the exception on stderr', () => {
  const customDir = join(testLogDir, 'throwing-validators');
  mkdirSync(customDir, { recursive: true });
  writeFileSync(join(customDir, 'speech-format.mjs'), `
export function getBlocks() {
  return [{ role: 'assistant', label: '===== 资讯助理 =====', line: 1,
    lines: ['', '🇷🇴 罗马尼亚财经新闻｜央行公布政策决定', '', '央行公布了利率决定。', '', '市场将关注后续数据。', '', '配图：https://cdn.example.com/event.jpg'] }];
}
export function createSpeechFormatTool() {
  return { handlers: { validate_speech_format() { throw new Error('validator-crash-marker'); } } };
}
`);
  writeFileSync(join(customDir, 'whatsapp-voice.mjs'), `
export function createWhatsAppVoiceTool() {
  return { handlers: { validate_whatsapp_voice() { return { issues: [] }; } } };
}
`);

  const promptId = 'validator-error-prompt-001';
  const result = runHook({ session_id: 'validator-error-session', prompt_id: promptId,
    last_assistant_message: 'trigger custom validator' }, {
    envOverrides: { WESTCOTT_VALIDATORS_DIR: customDir, WESTCOTT_STOP_GATE_LOG: testLogPath },
  });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /validator-crash-marker/);

  const records = readFileSync(testLogPath, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
  const record = records.find((entry) => entry.prompt_id === promptId);
  assert.ok(record);
  assert.equal(record.status, 'error');
  assert.equal(record.error_type, 'stop_gate_runtime_error');
  assert.equal(JSON.stringify(record).includes('validator-crash-marker'), false,
    'exception message should stay out of the privacy-preserving JSONL log');

  const retry = runHook({ stop_hook_active: true, last_assistant_message: 'trigger custom validator' }, {
    envOverrides: { WESTCOTT_VALIDATORS_DIR: customDir, WESTCOTT_STOP_GATE_LOG: testLogPath },
  });
  assert.equal(retry.status, 0, retry.stderr);
  assert.match(retry.stderr, /validator-crash-marker/);
  const decision = JSON.parse(retry.stdout);
  assert.equal(decision.continue, false);
  assert.match(decision.stopReason, /仍无法完成检查/);
});

test('writes structured block logs without storing dialogue, image URLs, or matched terms', () => {
  const promptId = 'log-test-prompt-001';
  const body = `${newsBlock({ footer: '' })}\n\n12男：家人们，先看收盘。`;
  const result = runHook({
    session_id: 'log-test-session-001',
    prompt_id: promptId,
    last_assistant_message: body,
  });
  assert.equal(result.status, 0);
  assert.equal(JSON.parse(result.stdout.trim()).decision, 'block');

  const records = readFileSync(process.env.WESTCOTT_STOP_GATE_LOG || testLogPath, 'utf8')
    .trim().split('\n').map((line) => JSON.parse(line));
  const record = records.find((entry) => entry.prompt_id === promptId);
  assert.ok(record, 'expected a structured record for the test prompt');
  assert.equal(record.status, 'blocked');
  assert.equal(record.hook_event, 'Stop');
  assert.equal(record.session_id, 'log-test-session-001');
  assert.ok(record.issues.some((issue) => issue.type === 'image_footer'));
  assert.ok(record.issues.some((issue) => issue.type === 'chinese_cliche'));
  assert.equal(JSON.stringify(record).includes('家人们'), false);
  assert.equal(JSON.stringify(record).includes('event.jpg'), false);
  assert.equal(JSON.stringify(record).includes(body), false);
});
