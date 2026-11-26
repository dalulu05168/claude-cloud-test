import { readFileSync } from 'node:fs';
import { getBlocks } from './speech-format.mjs';

// 用法：node tools/gap-check.mjs 稿件.txt [最少人数=3] [最多人数=6]
// 逐处统计助理或先生发言之间连续的成员发言条数，并按区间标出偏少或偏多的位置。
const [file, minArg = '3', maxArg = '6'] = process.argv.slice(2);
if (!file) {
  console.error('用法：node tools/gap-check.mjs 稿件.txt [最少人数=3] [最多人数=6]');
  process.exit(2);
}
const min = Number(minArg);
const max = Number(maxArg);
const blocks = getBlocks(readFileSync(file, 'utf8'));
const counts = { assistant: 0, teacher: 0, member: 0, other: 0 };
const runs = [];
let previous = null;
let run = null;
for (const block of blocks) {
  counts[block.role] = (counts[block.role] ?? 0) + 1;
  if (block.role === 'member') {
    if (!run) {
      run = { after: previous ? `${previous.label}（第${previous.line}行）` : '开头', line: block.line, count: 0 };
      runs.push(run);
    }
    run.count += 1;
  } else {
    run = null;
    previous = block;
  }
}
for (const entry of runs) {
  entry.status = entry.count < min ? '偏少' : entry.count > max ? '偏多' : '区间内';
}
const flagged = runs.filter((entry) => entry.status !== '区间内');
console.log(JSON.stringify({
  total: blocks.length, ...counts, range: [min, max],
  runs: runs.map(({ after, line, count, status }) => ({ after, line, count, status })),
  flagged: flagged.length,
}, null, 2));
