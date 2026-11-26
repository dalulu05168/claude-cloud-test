#!/usr/bin/env node

import { readFile } from 'node:fs/promises';
import { isAbsolute, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const FRIENDLY_NAMES = {
  'news_format.image_footer': '资讯配图行或链接格式',
  'news_format.image_url_mismatch': '配图链接与已核记录不一致',
  'news_format.paragraph_count': '新闻段落数量',
  'news_format.headline_format': '新闻标题格式',
  'news_format.country_flag': '国家与国旗不一致',
  'news_format.role_label': '资讯助理角色标记',
  'teacher_format.paragraph_count': 'David 发言段落数量',
  'teacher_format.role_label': 'David 角色标记',
  'voice.chinese_cliche': '中文群聊套话',
  'voice.market_slang': '股票黑话',
  'voice.hype': '炒作或收益口号',
  'voice.language': '非中文发言',
  'voice.emoji_density': '表情数量提示',
  'voice.member_length': '成员消息长度提示',
};

const USAGE = `用法：
  node analyze-stop-gate.mjs [日志路径] [--since YYYY-MM-DD] [--top N] [--json]

默认日志：$CLAUDE_PROJECT_DIR/.westcott-state/logs/stop-gate.jsonl
选项：
  --since DATE  只统计该 UTC 日期及之后的记录，例如 2026-11-01
  --top N       展示前 N 个原因，默认 10
  --json        输出机器可读 JSON
  --help        显示帮助

也可通过 WESTCOTT_STOP_GATE_LOG 环境变量指定默认日志文件。`;

function parseArgs(args) {
  const result = { path: null, since: null, top: 10, json: false, help: false };
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === '--json') result.json = true;
    else if (arg === '--help' || arg === '-h') result.help = true;
    else if (arg === '--since') {
      if (!args[index + 1] || args[index + 1].startsWith('-')) throw new Error('--since 需要 YYYY-MM-DD 日期。');
      result.since = args[++index];
    }
    else if (arg.startsWith('--since=')) result.since = arg.slice('--since='.length);
    else if (arg === '--top') result.top = Number(args[++index]);
    else if (arg.startsWith('--top=')) result.top = Number(arg.slice('--top='.length));
    else if (arg.startsWith('-')) throw new Error(`未知选项：${arg}`);
    else if (result.path === null) result.path = arg;
    else throw new Error(`只允许一个日志路径：${arg}`);
  }

  if (result.since !== null) {
    const parsedDate = /^\d{4}-\d{2}-\d{2}$/.test(result.since)
      ? new Date(`${result.since}T00:00:00.000Z`)
      : null;
    if (!parsedDate || Number.isNaN(parsedDate.valueOf()) || parsedDate.toISOString().slice(0, 10) !== result.since) {
      throw new Error('--since 必须是有效的 YYYY-MM-DD 日期。');
    }
  }
  if (!Number.isInteger(result.top) || result.top < 1 || result.top > 100) {
    throw new Error('--top 必须是 1 到 100 之间的整数。');
  }
  return result;
}

function defaultLogPath() {
  const root = process.env.CLAUDE_PROJECT_DIR || process.cwd();
  if (process.env.WESTCOTT_STOP_GATE_LOG && process.env.WESTCOTT_STOP_GATE_LOG !== 'off') {
    const configured = process.env.WESTCOTT_STOP_GATE_LOG;
    return isAbsolute(configured) ? configured : resolve(root, configured);
  }
  return resolve(root, '.westcott-state', 'logs', 'stop-gate.jsonl');
}

function blankDay() {
  return { passed: 0, blocked: 0, error: 0, skipped: 0, other: 0 };
}

export function analyzeLogText(text, { since = null, top = 10 } = {}) {
  const sourceLines = text.split(/\r?\n/);
  const malformed = [];
  const records = [];

  for (const [index, line] of sourceLines.entries()) {
    if (!line.trim()) continue;
    try {
      const record = JSON.parse(line);
      if (!record || typeof record !== 'object' || Array.isArray(record)) throw new Error('not_object');
      records.push({ line: index + 1, record });
    } catch {
      malformed.push(index + 1);
    }
  }

  const selected = records.filter(({ record }) => {
    if (!since) return true;
    return typeof record.timestamp === 'string' && record.timestamp.slice(0, 10) >= since;
  });

  const statusCounts = { passed: 0, blocked: 0, error: 0, skipped: 0, other: 0 };
  const reasonCounts = new Map();
  const daily = new Map();
  const promptBlocks = new Map();

  for (const { record } of selected) {
    const status = Object.hasOwn(statusCounts, record.status) ? record.status : 'other';
    statusCounts[status] += 1;

    const date = typeof record.timestamp === 'string' && /^\d{4}-\d{2}-\d{2}/.test(record.timestamp)
      ? record.timestamp.slice(0, 10)
      : 'unknown-date';
    const day = daily.get(date) ?? blankDay();
    day[status] += 1;
    daily.set(date, day);

    if (record.status === 'blocked' && record.session_id && record.prompt_id) {
      const key = `${record.session_id}\u0000${record.prompt_id}`;
      promptBlocks.set(key, (promptBlocks.get(key) ?? 0) + 1);
    }

    if (Array.isArray(record.issues)) {
      for (const issue of record.issues) {
        if (!issue || typeof issue !== 'object') continue;
        const category = typeof issue.category === 'string' ? issue.category : 'unknown';
        const type = typeof issue.type === 'string' ? issue.type : 'unknown';
        const code = `${category}.${type}`;
        reasonCounts.set(code, (reasonCounts.get(code) ?? 0) + 1);
      }
    } else if (record.issue_counts && typeof record.issue_counts === 'object') {
      for (const [code, count] of Object.entries(record.issue_counts)) {
        if (Number.isFinite(count) && count > 0) reasonCounts.set(code, (reasonCounts.get(code) ?? 0) + count);
      }
    }
  }

  const topReasons = [...reasonCounts.entries()]
    .map(([code, count]) => ({ code, label: FRIENDLY_NAMES[code] ?? code, count }))
    .sort((a, b) => b.count - a.count || a.code.localeCompare(b.code))
    .slice(0, top);
  const dailyTrend = [...daily.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, counts]) => ({ date, ...counts }));
  const usableChecks = statusCounts.passed + statusCounts.blocked;

  return {
    schema_version: 1,
    filter: { since_utc_date: since, top },
    totals: {
      records: selected.length,
      passed: statusCounts.passed,
      blocked: statusCounts.blocked,
      errors: statusCounts.error,
      skipped: statusCounts.skipped,
      other: statusCounts.other,
      malformed_lines: malformed.length,
      malformed_line_numbers: malformed,
      block_rate: usableChecks ? Number((statusCounts.blocked / usableChecks).toFixed(4)) : null,
      prompts_blocked_more_than_once: [...promptBlocks.values()].filter((count) => count > 1).length,
      max_blocks_for_one_prompt: Math.max(0, ...promptBlocks.values()),
    },
    top_reasons: topReasons,
    daily_trend: dailyTrend,
  };
}

function renderText(report) {
  const { totals } = report;
  const rate = totals.block_rate === null ? 'N/A' : `${(totals.block_rate * 100).toFixed(1)}%`;
  const lines = [
    'Westcott Stop Gate 日志分析',
    `记录：${totals.records} | 通过：${totals.passed} | 拦截：${totals.blocked} | 错误：${totals.errors} | 跳过：${totals.skipped}`,
    `拦截率（仅通过+拦截）：${rate}`,
    `重复拦截的 prompt：${totals.prompts_blocked_more_than_once} | 单个 prompt 最大拦截次数：${totals.max_blocks_for_one_prompt}`,
    `无效 JSONL 行：${totals.malformed_lines}${totals.malformed_line_numbers.length ? `（行号 ${totals.malformed_line_numbers.join(', ')}）` : ''}`,
    '',
    '常见拦截原因：',
  ];

  if (!report.top_reasons.length) lines.push('  无');
  else for (const reason of report.top_reasons) lines.push(`  ${reason.count} 次  ${reason.label} [${reason.code}]`);

  lines.push('', '按日趋势（UTC）：');
  if (!report.daily_trend.length) lines.push('  无');
  else {
    for (const day of report.daily_trend) {
      lines.push(`  ${day.date} 通过 ${day.passed} / 拦截 ${day.blocked} / 错误 ${day.error} / 跳过 ${day.skipped}`);
    }
  }
  return lines.join('\n');
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    console.log(USAGE);
    return;
  }

  const filename = options.path
    ? (isAbsolute(options.path) ? options.path : resolve(process.cwd(), options.path))
    : defaultLogPath();
  const text = await readFile(filename, 'utf8');
  const report = analyzeLogText(text, options);
  console.log(options.json ? JSON.stringify(report, null, 2) : renderText(report));
}

const invokedDirectly = process.argv[1]
  && import.meta.url === pathToFileURL(resolve(process.argv[1])).href;

if (invokedDirectly) {
  main().catch((error) => {
    console.error(`[stop-gate analysis] ${error?.message ?? String(error)}`);
    process.exitCode = 1;
  });
}
