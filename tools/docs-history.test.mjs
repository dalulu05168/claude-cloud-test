import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildHistoryIndex, createDocsHistory, searchHistory } from './docs-history.mjs';

const paragraph = (...pieces) => ({ paragraph: { elements: pieces.map((content) => ({ textRun: { content } })) } });
const tab = (tabId, title, blocks = [], childTabs = []) => ({
  tabProperties: { tabId, title }, documentTab: { body: { content: blocks } }, childTabs,
});
const source = {
  documentId: 'private_history', revisionId: 'r1', tabs: [
    tab('t.month', '2026-11 | D资讯助理月度检索目录', [paragraph('目录文字\n')], [
      tab('t.first', '2026-11-24', [paragraph('【晚上】\n'), paragraph('BVB ', '[能源] 国债\n')]),
      tab('t.second', '2026-11-25', [paragraph('【晚上】\n'), paragraph('BVB [能源] 定存\n')]),
      tab('t.empty', '2026-11-26', [paragraph('2026-11-26 | D资讯助理每日档案\n'),
        paragraph('状态：空白档案（尚无正文）\n'), paragraph('原始记录：\n')]),
      tab('t.today', '2026-11-27', [{ table: { tableRows: [{ tableCells: [
        { content: [paragraph('当日材料 国债\n')] },
      ] }] } }]),
    ]),
  ],
};

test('indexes native nested date tabs and returns exact source links for date and phrase matches', () => {
  const { index, stats } = buildHistoryIndex({ content: source });
  assert.deepEqual(stats, { added: 3, updated: 0, unchanged: 0, removed: 0, retained: 0, total: 3 });
  assert.deepEqual(index.pages.map((page) => page.tabId), ['t.today', 't.second', 't.first']);
  const prior = searchHistory(index, { query: 'bvb [能源]', before: '2026-11-27', limit: 2 });
  assert.deepEqual(prior.map((page) => page.date), ['2026-11-25', '2026-11-24']);
  assert.equal(prior[0].excerpt, '【晚上】\nBVB [能源] 定存');
  assert.equal(prior[0].url, 'https://docs.google.com/document/d/private_history/edit?tab=t.second');
  assert.equal(prior[0].sourceRevision, 'r1');
  assert.equal(prior[0].matchesSnapshotRevision, true);
  assert.match(prior[0].sha256, /^[a-f0-9]{64}$/);
  assert.equal(searchHistory(index, { date: '2026-11-27' })[0].excerpt, '当日材料 国债');
  assert.deepEqual(searchHistory(index, { query: '目录文字' }), []);
  assert.deepEqual(searchHistory(index, { query: '[不存在]' }), []);
});

test('connector update changes only fetched date pages and keeps original per-page revisions', () => {
  const { index: initial } = buildHistoryIndex(source);
  const snapshot = {
    metadata: { documentId: source.documentId, revisionId: 'r2', tabs: [
      { tabId: 't.second', title: '2026-11-25', parentTabId: 't.month' },
      { tabId: 't.empty', title: '2026-11-26', parentTabId: 't.month' },
    ] },
    pages: [{ documentId: source.documentId, revisionId: 'r2', tabId: 't.second',
      paragraphs: [{ text: '【晚上】\n' }, { text: 'BVB [能源] 通胀\n' }] }],
  };
  const { index, stats } = buildHistoryIndex(snapshot, initial);
  assert.deepEqual(stats, { added: 0, updated: 1, unchanged: 0, removed: 0, retained: 2, total: 3 });
  assert.equal(searchHistory(index, { query: '通胀' })[0].sourceRevision, 'r2');
  assert.equal(searchHistory(index, { query: '国债' })[0].sourceRevision, 'r1');
  assert.equal(searchHistory(index, { query: '国债' })[0].matchesSnapshotRevision, false);
  assert.equal(buildHistoryIndex(snapshot, index).stats.unchanged, 1);
  const blank = { ...snapshot, pages: [{ ...snapshot.pages[0], paragraphs: [] }] };
  const { index: removed, stats: removedStats } = buildHistoryIndex(blank, index);
  assert.equal(removedStats.removed, 1);
  assert.deepEqual(searchHistory(removed, { date: '2026-11-25' }), []);
  assert.equal(buildHistoryIndex(source, removed).stats.added, 1);
  assert.equal(buildHistoryIndex(source, index).stats.updated, 1);
  const mismatch = { ...snapshot, pages: [{ ...snapshot.pages[0], revisionId: 'other' }] };
  assert.throws(() => buildHistoryIndex(mismatch, index), /不同文档修订/);
  assert.throws(() => buildHistoryIndex({ ...source, documentId: 'other_doc' }, index), /不同文档/);
});

test('missing source bodies and malformed searches fail instead of refreshing a misleading index', () => {
  const exportedMetadataOnly = { documentId: source.documentId, tabs: [{ tabId: 't.first', title: '2026-11-24' }] };
  assert.throws(() => buildHistoryIndex(exportedMetadataOnly), /缺少 2026-11-24 正文/);
  const { index } = buildHistoryIndex(source);
  assert.throws(() => searchHistory(index, { date: '2026-02-30' }), /有效的 YYYY-MM-DD/);
  assert.throws(() => searchHistory({ ...index, pages: [{ ...index.pages[0], body: '其他正文' }] }), /已损坏/);
});

test('CLI persists and reloads an index, with failed and concurrent updates leaving source and index intact', () => {
  const dir = mkdtempSync(join(tmpdir(), 'westcott-docs-history-'));
  const path = (file) => join(dir, file);
  const script = fileURLToPath(new URL('./docs-history.mjs', import.meta.url));
  const run = (...args) => spawnSync(process.execPath, [script, ...args], { encoding: 'utf8' });
  try {
    const sourcePath = path('source.json');
    const indexPath = path('private-index.json');
    const raw = `\uFEFF${JSON.stringify(source)}`;
    writeFileSync(sourcePath, raw);
    const initial = run('update', sourcePath, indexPath);
    assert.equal(initial.status, 0, initial.stderr);
    assert.equal(JSON.parse(initial.stdout).total, 3);
    const result = run('search', indexPath, '--query', '国债', '--before', '2026-11-27', '--limit', '1');
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout)[0].tabId, 't.first');
    const originalIndex = readFileSync(indexPath, 'utf8');
    writeFileSync(path('invalid.json'), '{');
    assert.equal(run('update', path('invalid.json'), indexPath).status, 2);
    writeFileSync(`${indexPath}.lock`, '');
    assert.equal(run('update', sourcePath, indexPath).status, 2);
    assert.equal(run('update', sourcePath, sourcePath).status, 2);
    assert.equal(readFileSync(indexPath, 'utf8'), originalIndex);
    assert.equal(readFileSync(sourcePath, 'utf8'), raw);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('MCP uses only the configured snapshot and distinguishes stale, empty and missing results', async () => {
  const stateDir = mkdtempSync(join(tmpdir(), 'westcott-history-mcp-'));
  const schema = (properties) => ({ type: 'object', additionalProperties: false, properties });
  const text = (maxLength) => ({ type: 'string', maxLength });
  const validateDate = (date) => assert.equal(date, '2026-11-27');
  try {
    const { definitions, handlers } = createDocsHistory({ text, schema, dateField: text(10), validateDate, stateDir });
    assert.equal(definitions[0].annotations.readOnlyHint, true);
    assert.equal(Object.hasOwn(definitions[0].inputSchema.properties, 'file'), false);
    const search = handlers.search_docs_history;
    assert.equal((await search({})).status, 'index_missing');
    const indexPath = join(stateDir, 'history-index.json');
    const content = JSON.stringify(buildHistoryIndex(source).index);
    writeFileSync(indexPath, content);
    const response = await search({ query: '国债', before: '2026-11-27', limit: 1 });
    assert.equal(response.status, 'ok');
    assert.equal(response.source_status, 'local_snapshot');
    assert.equal(response.updated_at, statSync(indexPath).mtime.toISOString());
    assert.equal(response.results[0].tabId, 't.first');
    assert.deepEqual(await search({ query: '不存在' }), {
      status: 'no_results', source_status: 'local_snapshot', updated_at: response.updated_at, results: [],
    });
    assert.equal(readFileSync(indexPath, 'utf8'), content);
  } finally {
    rmSync(stateDir, { recursive: true, force: true });
  }
});
