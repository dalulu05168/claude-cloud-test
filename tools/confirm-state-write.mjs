#!/usr/bin/env node

/**
 * PreToolUse guard for persistent Westcott state changes.
 *
 * Claude Code invokes this hook before MCP tools. Read-only tools return an
 * empty JSON object. Mutating tools return permissionDecision=ask so the
 * current user must approve the actual state change; a model-provided
 * approval/confirmation string is never treated as proof of user consent.
 */

const MUTATING_TOOLS = new Set([
  'save_agenda',
  'save_draft',
  'record_adoption',
  'complete_lesson',
  'save_scene_checkpoint',
  'save_handoff',
  'save_fact_check',
  'save_member_profile',
  'save_member_activity',
  'import_mobile_handoff',
  'build_lesson_kit',
]);

// A mobile handoff can contain 500,000 characters plus JSON escaping and metadata.
const MAX_INPUT_BYTES = 4 * 1024 * 1024;

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    process.stdin.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_INPUT_BYTES) {
        reject(new Error('PreToolUse input is too large.'));
        process.stdin.destroy();
        return;
      }
      chunks.push(chunk);
    });
    process.stdin.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    process.stdin.on('error', reject);
  });
}

function toolShortName(toolName) {
  if (typeof toolName !== 'string') return null;
  const parts = toolName.split('__');
  return parts.at(-1) || null;
}

function emit(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

async function main() {
  let input;
  try {
    input = JSON.parse(await readStdin());
    if (typeof input?.tool_name !== 'string' || !input.tool_name) {
      throw new Error('PreToolUse input is missing tool_name.');
    }
  } catch (error) {
    console.error(`[Westcott state guard] invalid hook input: ${error?.message ?? String(error)}`);
    process.exitCode = 2;
    return;
  }

  const name = toolShortName(input?.tool_name);
  if (!MUTATING_TOOLS.has(name)) {
    emit({});
    return;
  }

  emit({
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'ask',
      permissionDecisionReason: `即将调用 ${name} 修改 Westcott 持久化状态。请确认本次具体写入、范围和状态；模型填写的 approval/confirmation 文本不等同于用户确认。`,
    },
  });
}

main().catch((error) => {
  console.error(`[Westcott state guard] ${error?.message ?? String(error)}`);
  process.exitCode = 2;
});
