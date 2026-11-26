#!/usr/bin/env node

/**
 * Claude Code Stop hook for the Westcott project.
 *
 * Reuses project validators to check likely Westcott news blocks, David blocks,
 * and blocking voice issues such as Chinese clichés, stock slang, hype slogans,
 * and non-Chinese dialogue.
 *
 * JSONL logs contain metadata and issue codes only; they never include the
 * output body, image URL, or matched sensitive term. The image check validates
 * the HTTP(S) URL shape, but does not fetch the URL or prove the response is an
 * image or that the image matches the news event.
 */

import { access, appendFile, mkdir } from 'node:fs/promises';
import { dirname, isAbsolute, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const MAX_INPUT_BYTES = 1_000_000;
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const BUNDLED_VALIDATORS_DIR = resolve(SCRIPT_DIR, 'vendor');
const DEFAULT_PROJECT_DIR = process.cwd();
let logContext = { session_id: null, prompt_id: null };
let retryActive = false;

function readStdin() {
  return new Promise((resolveInput, reject) => {
    const chunks = [];
    let size = 0;
    process.stdin.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_INPUT_BYTES) {
        reject(new Error(`Stop hook input exceeds ${MAX_INPUT_BYTES} bytes.`));
        process.stdin.destroy();
        return;
      }
      chunks.push(chunk);
    });
    process.stdin.on('end', () => resolveInput(Buffer.concat(chunks).toString('utf8')));
    process.stdin.on('error', reject);
  });
}

function outputDecision(decision) {
  process.stdout.write(`${JSON.stringify(decision)}\n`);
}

function labelName(label) {
  return label.replace(/^=+\s*/, '').replace(/\s*=+$/, '').trim();
}

function isNewsCandidate(block) {
  const name = labelName(block.label);
  const assistantLike = block.role === 'assistant' || /资讯助理|助理/u.test(name);
  if (!assistantLike) return false;

  const lines = block.lines.map((line) => line.trim()).filter(Boolean);
  const hasPictureFooter = lines.some((line) => /^配图\s*[：:]/u.test(line));
  const hasNewsHeadline = lines.some((line) => /财经(?:新闻|资讯快报|快报)\s*[｜|]/u.test(line));
  return hasPictureFooter || hasNewsHeadline;
}

function buildSingleBlock(block) {
  return `${block.label}\n\n${block.lines.join('\n')}`;
}

function makeValidator(factory) {
  const text = (maxLength) => ({ type: 'string', minLength: 1, maxLength });
  const schema = (properties, required = []) => ({
    type: 'object', properties, required, additionalProperties: false,
  });
  return factory({ text, schema });
}

function classifySpeechIssue(message) {
  if (/配图链接与/u.test(message)) return 'image_url_mismatch';
  if (/配图/u.test(message)) return 'image_footer';
  if (/正文需/u.test(message)) return 'paragraph_count';
  if (/国旗/u.test(message)) return 'country_flag';
  if (/首段/u.test(message)) return 'headline_format';
  if (/发言标记|角色标记/u.test(message)) return 'role_label';
  return 'speech_format';
}

function projectDir() {
  return process.env.CLAUDE_PROJECT_DIR || DEFAULT_PROJECT_DIR;
}

async function loadValidatorModules() {
  const configured = process.env.WESTCOTT_VALIDATORS_DIR;
  const candidates = [
    ...(configured ? [isAbsolute(configured) ? configured : resolve(projectDir(), configured)] : []),
    resolve(projectDir(), 'tools'),
    BUNDLED_VALIDATORS_DIR,
  ];
  const failures = [];
  for (const directory of [...new Set(candidates)]) {
    try {
      const speechPath = resolve(directory, 'speech-format.mjs');
      const voicePath = resolve(directory, 'whatsapp-voice.mjs');
      await Promise.all([access(speechPath), access(voicePath)]);
      return await Promise.all([
        import(pathToFileURL(speechPath).href),
        import(pathToFileURL(voicePath).href),
      ]);
    } catch (error) {
      failures.push({ directory, name: error?.name ?? 'Error', message: error?.message ?? String(error) });
    }
  }
  const error = new Error('Could not load the Westcott validators from the configured, project, or bundled directories. Set WESTCOTT_STOP_GATE_DEBUG=1 to print per-directory diagnostics.');
  error.code = 'validator_modules_missing';
  error.diagnostics = failures;
  throw error;
}

function logFilePath() {
  const configured = process.env.WESTCOTT_STOP_GATE_LOG;
  if (configured === 'off') return null;
  if (configured) return isAbsolute(configured) ? configured : resolve(projectDir(), configured);
  return resolve(projectDir(), '.westcott-state', 'logs', 'stop-gate.jsonl');
}

async function writeLog({ status, message = '', newsBlocks = 0, teacherBlocks = 0,
  issues = [], errorType = null }) {
  const filename = logFilePath();
  if (!filename) return;

  const counts = {};
  for (const issue of issues) {
    const key = `${issue.category}.${issue.type}`;
    counts[key] = (counts[key] ?? 0) + 1;
  }

  const record = {
    schema_version: 1,
    timestamp: new Date().toISOString(),
    hook_event: 'Stop',
    status,
    session_id: logContext.session_id,
    prompt_id: logContext.prompt_id,
    output_chars: typeof message === 'string' ? message.length : 0,
    news_blocks: newsBlocks,
    teacher_blocks: teacherBlocks,
    issue_counts: counts,
    issues: issues.map(({ category, type, line, severity = 'block' }) => ({
      category,
      type,
      ...(Number.isInteger(line) ? { line } : {}),
      severity,
    })),
    ...(errorType ? { error_type: errorType } : {}),
  };

  try {
    await mkdir(dirname(filename), { recursive: true, mode: 0o700 });
    await appendFile(filename, `${JSON.stringify(record)}\n`, { encoding: 'utf8', mode: 0o600 });
  } catch (error) {
    // Logging is useful for diagnosis but must not hide the validation result.
    console.error(`[Westcott Stop gate] Could not write structured log: ${error?.message ?? String(error)}`);
  }
}

async function main() {
  const raw = await readStdin();
  let hookInput;
  try {
    hookInput = JSON.parse(raw);
  } catch {
    const error = new Error('Stop hook received invalid JSON input; refusing to skip output checks.');
    error.code = 'invalid_hook_json';
    throw error;
  }
  retryActive = hookInput?.stop_hook_active === true;

  logContext = {
    session_id: typeof hookInput?.session_id === 'string' ? hookInput.session_id : null,
    prompt_id: typeof hookInput?.prompt_id === 'string' ? hookInput.prompt_id : null,
  };

  const message = hookInput?.last_assistant_message;
  if (typeof message !== 'string' || !message.trim()) {
    await writeLog({ status: 'skipped' });
    return;
  }

  // Load the project's tested validators only when there is output to inspect.
  const [speechModule, voiceModule] = await loadValidatorModules();
  const validateSpeechFormat = makeValidator(speechModule.createSpeechFormatTool)
    .handlers.validate_speech_format;
  const validateWhatsAppVoice = makeValidator(voiceModule.createWhatsAppVoiceTool)
    .handlers.validate_whatsapp_voice;

  const blocks = speechModule.getBlocks(message);
  const issues = [];
  const addIssue = (category, type, messageText, line, severity = 'block') => {
    issues.push({ category, type, message: messageText, line, severity });
  };
  let checkedNews = 0;
  let checkedTeachers = 0;

  for (const block of blocks) {
    if (!isNewsCandidate(block)) continue;
    checkedNews += 1;

    const name = labelName(block.label);
    if (name !== '资讯助理' && name !== '助理') {
      addIssue('news_format', 'role_label', '新闻块的角色标记应为「===== 资讯助理 =====」。', block.line);
    }

    const result = validateSpeechFormat({ body: buildSingleBlock(block), mode: 'news' });
    for (const issue of result.issues) {
      const line = Math.max(1, block.line + (issue.line ?? 1) - 1);
      addIssue('news_format', classifySpeechIssue(issue.message), issue.message, line);
    }
  }

  const teacherBlocks = blocks.filter((block) => block.role === 'teacher'
    || /^(?:先生|David\s*先生)$/iu.test(labelName(block.label)));
  checkedTeachers = teacherBlocks.length;
  if (checkedTeachers) {
    const teacherResult = validateSpeechFormat({ body: message, mode: 'teacher' });
    for (const issue of teacherResult.issues) {
      addIssue('teacher_format', classifySpeechIssue(issue.message), issue.message, issue.line);
    }
  }

  // Apply the project's blocked voice rules to structured dialogue only.
  // Free-form answers without role blocks are not dialogue and should not be
  // blocked merely for discussing a listed term.
  if (blocks.length) {
    const voiceResult = validateWhatsAppVoice({ body: message });
    for (const issue of voiceResult.issues) {
      if (issue.severity !== 'block') continue;
      const term = issue.term ? `（命中「${issue.term}」）` : '';
      addIssue('voice', issue.type, `${issue.message}${term}`, issue.line, issue.severity);
    }
  }

  await writeLog({
    status: issues.length ? (retryActive ? 'error' : 'blocked') : 'passed',
    message,
    newsBlocks: checkedNews,
    teacherBlocks: checkedTeachers,
    issues,
    errorType: issues.length && retryActive ? 'validation_retry_unresolved' : null,
  });

  if (issues.length) {
    const details = [...new Set(issues.map((issue) => {
      const line = issue.line ? `第 ${issue.line} 行：` : '';
      return `- ${line}${issue.message}`;
    }))].slice(0, 20).join('\n');
    const more = issues.length > 20 ? `\n- 另有 ${issues.length - 20} 项问题未列出。` : '';
    const report = `Westcott Stop gate 未通过（检查到 ${checkedNews} 条疑似资讯新闻、${checkedTeachers} 条 David 发言）。\n${details}${more}\n配图检查仅验证配图行和 HTTP(S) URL 格式。图片可访问性与新闻相关性需另行核实。`;
    const decision = retryActive
      ? { continue: false, stopReason: `本轮自动修订后仍未通过检查，当前稿件待人工核对。\n${report}` }
      : { decision: 'block', reason: `请修正以下问题，再结束本轮。\n${report}` };
    outputDecision(decision);
  }
}

main().catch(async (error) => {
  await writeLog({ status: 'error', errorType: error?.code ?? 'stop_gate_runtime_error' });
  const debug = process.env.WESTCOTT_STOP_GATE_DEBUG === '1';
  const diagnostics = Array.isArray(error?.diagnostics)
    ? `\n${error.diagnostics.map(({ directory, name, message }) => `- ${directory}: ${name}: ${message}`).join('\n')}`
    : '';
  const details = debug ? `${error?.stack ?? error?.message ?? String(error)}${diagnostics}` : (error?.message ?? String(error));
  console.error(`[Westcott Stop gate] ${details}`);
  if (retryActive) {
    outputDecision({ continue: false, stopReason: 'Westcott Stop gate 在重试时仍无法完成检查，当前稿件待人工核对。' });
    return;
  }
  // Exit code 2 blocks a Claude Code Stop hook, including on validator failure.
  process.exitCode = 2;
});
