import { readFileSync } from 'node:fs';

// 用法：node tools/read-doc-day.mjs 导出文件.txt 2026-11-26 [--summary-only]
// 从谷歌文档的纯文本导出里只切出某一天的那一页，到下一个日期标题就停。
// 找不到这一天、或这一页是空白档案，会直接说明，不猜测。
const args = process.argv.slice(2);
const summaryOnly = args.includes('--summary-only');
const [file, date] = args.filter((arg) => !arg.startsWith('--'));
if (!file || !/^\d{4}-\d{2}-\d{2}$/.test(date ?? '')) {
  console.error('用法：node tools/read-doc-day.mjs 导出文件.txt YYYY-MM-DD [--summary-only]');
  process.exit(2);
}

const text = readFileSync(file, 'utf8').replace(/^﻿/, '').replace(/\r\n?/g, '\n');
const lines = text.split('\n');
const archiveHead = /^(\d{4}-\d{2}-\d{2}) \| D资讯助理每日档案\s*$/;
const hashHead = /^# (\d{4}-\d{2}-\d{2})\s*$/;
const monthHead = /^\d{4}-\d{2} \| D资讯助理月度检索目录\s*$/;
const headOf = (line) => archiveHead.exec(line)?.[1] ?? hashHead.exec(line)?.[1] ?? null;

const available = [];
let start = -1;
for (const [index, line] of lines.entries()) {
  const day = headOf(line);
  if (!day) continue;
  available.push(day);
  if (day === date && start < 0) start = index;
}
if (start < 0) {
  const indexLine = lines.find((line) => line.startsWith(`${date} | `));
  if (indexLine && /空白|待补充|尚无正文/.test(indexLine)) {
    console.log(`${date} 只在月度目录里出现，是空白日期页，没有正文：${indexLine.trim()}`);
    process.exit(0);
  }
  if (lines.some((line) => line.startsWith('星期六不建档') && line.includes(date))) {
    console.log(`${date} 是星期六，文档不建档。`);
    process.exit(0);
  }
  const near = [...new Set(available)].filter((day) => Math.abs(Date.parse(day) - Date.parse(date)) <= 3 * 864e5);
  console.error(`找不到 ${date} 这一页。文档里有正文标题的日期共 ${new Set(available).size} 个，前后三天内的：${near.join('、') || '无'}`);
  process.exit(1);
}
let end = lines.length;
for (let index = start + 1; index < lines.length; index += 1) {
  if (headOf(lines[index]) || monthHead.test(lines[index])) { end = index; break; }
}
const page = lines.slice(start, end).map((line) => line.replace(/[ \t]+$/, '')).join('\n')
  .replace(/\n{3,}/g, '\n\n').trim();

if (/空白档案|空白日期页|尚无正文/.test(page) && !/=====/.test(page)) {
  console.log(`${date} 这一页是空白档案，没有正文。`);
  process.exit(0);
}

const evening = page.indexOf('【晚上】');
const afternoonMark = page.indexOf('大家下午好');
const afternoon = afternoonMark < 0 ? -1 : Math.max(page.lastIndexOf('===== 资讯助理 =====', afternoonMark), 0);
const cuts = [['上午', page.indexOf('【上午】')], ['下午', afternoon], ['晚上', evening]]
  .filter(([, at]) => at >= 0).sort((a, b) => a[1] - b[1]);
const count = (segment) => ({
  助理: (segment.match(/^={3,}\s*资讯助理\s*={3,}/gm) ?? []).length,
  David: (segment.match(/^={3,}\s*David\s*先生\s*={3,}/gm) ?? []).length,
  成员: (segment.match(/^\s*\d{2}[男女]\s*[：:]/gm) ?? []).length,
});
const sessions = cuts.map(([name, at], index) => {
  const segment = page.slice(at, cuts[index + 1]?.[1] ?? page.length);
  return { 场次: name, 字数: segment.length, ...count(segment) };
});
const noAfternoonLabel = afternoon >= 0 && page.indexOf('【下午】') < 0;
console.log(`# ${date}｜整页 ${page.length} 字｜场次：${sessions.map((s) => `${s.场次}${s.助理 + s.David + s.成员}条`).join('、')}`);
for (const s of sessions) console.log(`  ${s.场次}：助理 ${s.助理}、David ${s.David}、成员 ${s.成员}，${s.字数} 字`);
if (noAfternoonLabel) console.log('  注：下午没有【下午】标记，按“大家下午好”所在的助理块起算。');
if (!summaryOnly) console.log(`\n${page}`);
