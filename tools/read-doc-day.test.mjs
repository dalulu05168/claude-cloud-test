import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const dir = mkdtempSync(join(tmpdir(), 'read-doc-day-'));
const file = join(dir, 'doc.txt');
writeFileSync(file, [
  '2026-11-25 | D资讯助理每日档案', '【上午】', '===== 资讯助理 =====', '早上好', '01男：', '早',
  '2026-11-26 | D资讯助理每日档案', '【上午】', '===== 资讯助理 =====', '早上好', '15男：', '早',
  '===== 资讯助理 =====', '大家下午好', '【晚上】', '===== David先生 =====', '晚上好',
  '2026-12 | D资讯助理月度检索目录', '2026-12-01 | 周二 | 空白日期页（尚无正文）',
  '2026-12-01 | D资讯助理每日档案', '状态：空白档案（尚无正文）', '原始记录：',
].join('\n'));
const run = (...args) => spawnSync(process.execPath, ['tools/read-doc-day.mjs', file, ...args], { encoding: 'utf8' });

const day = run('2026-11-26');
assert.equal(day.status, 0);
assert.ok(day.stdout.includes('15男：') && day.stdout.includes('David先生'));
assert.ok(!day.stdout.includes('01男：') && !day.stdout.includes('2026-12-01'), '不能带出前后日期的内容');
assert.ok(run('2026-12-01').stdout.includes('空白档案'));
assert.equal(run('2026-11-30').status, 1);
console.log('read-doc-day: ok');
