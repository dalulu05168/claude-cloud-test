import { createHash, randomUUID } from 'node:crypto';
import { closeSync, openSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { lstat, open } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const dayTitle = /^(\d{4}-\d{2}-\d{2})(?:\s+\|\s+D资讯助理每日档案)?$/;
const documentIdPattern = /^[A-Za-z0-9_-]+$/;
const tabIdPattern = /^[A-Za-z0-9_.-]+$/;
const hash = (text) => createHash('sha256').update(text).digest('hex');

function validDate(date) {
  if (typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return false;
  const time = Date.parse(`${date}T00:00:00Z`);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === date;
}

function dateTabs(tabs, found = new Map()) {
  if (!Array.isArray(tabs)) throw new Error('缺少 Google Docs 页签列表。');
  for (const tab of tabs) {
    const properties = tab?.tabProperties ?? tab;
    const date = dayTitle.exec(properties?.title?.trim() ?? '')?.[1];
    if (validDate(date)) {
      const { tabId } = properties;
      if (typeof tabId !== 'string' || !tabIdPattern.test(tabId) || found.has(tabId)) {
        throw new Error('日期页缺少唯一的 tabId。');
      }
      found.set(tabId, { date, tab });
    }
    if (tab?.childTabs) dateTabs(tab.childTabs, found);
  }
  return found;
}

function nativeParagraphs(blocks) {
  return blocks.flatMap((block) => {
    if (block.paragraph) {
      return [block.paragraph.elements.map((element) => element.textRun?.content ?? '').join('')];
    }
    return (block.table?.tableRows ?? []).flatMap((row) =>
      row.tableCells.flatMap((cell) => nativeParagraphs(cell.content)));
  });
}

function bodyText(paragraphs, date) {
  const body = paragraphs.map((line) => line.replace(/\r\n?/g, '\n').replace(/\n$/, '')).join('\n').trim();
  const content = body.split('\n').map((line) => line.trim()).filter(Boolean);
  const isPlaceholder = content.every((line) =>
    dayTitle.exec(line)?.[1] === date
    || line === '原始记录：'
    || /^(?:状态\s*[：:]\s*)?(?:空白档案|空白日期页|尚无正文|待补充)(?:[（(].*[）)])?$/.test(line));
  return isPlaceholder ? '' : body;
}

function revisionOf(value) {
  if (value == null) return null;
  if (typeof value !== 'string' || !value) throw new Error('文档修订编号无效。');
  return value;
}

function validateIndex(index) {
  if (index?.version !== 1 || typeof index.documentId !== 'string'
    || !documentIdPattern.test(index.documentId) || !Array.isArray(index.pages)) {
    throw new Error('无法识别历史索引。');
  }
  revisionOf(index.snapshotRevision);
  const seen = new Set();
  for (const page of index.pages) {
    if (!validDate(page?.date) || typeof page.tabId !== 'string' || !tabIdPattern.test(page.tabId)
      || seen.has(page.tabId) || typeof page.body !== 'string' || !page.body
      || page.sha256 !== hash(page.body)) throw new Error('历史索引中的日期页已损坏。');
    revisionOf(page.sourceRevision);
    seen.add(page.tabId);
  }
}

export function buildHistoryIndex(snapshot, previous = null) {
  const partial = Array.isArray(snapshot?.pages) && snapshot?.metadata != null;
  const source = partial ? snapshot.metadata : snapshot?.content ?? snapshot;
  const documentId = source?.documentId ?? snapshot?.documentId;
  if (typeof documentId !== 'string' || !documentIdPattern.test(documentId)) {
    throw new Error('导出缺少有效的 Google Docs documentId。');
  }
  if (previous) {
    validateIndex(previous);
    if (previous.documentId !== documentId) throw new Error('索引与导出来自不同文档。');
  }
  const sourceRevision = revisionOf(source.revisionId ?? snapshot.revisionId);
  const tabs = dateTabs(source.tabs);
  if (!tabs.size) throw new Error('导出中没有有效日期页签。');
  const oldPages = new Map((previous?.pages ?? []).map((page) => [page.tabId, page]));
  const pages = partial ? new Map(oldPages) : new Map();
  const touched = new Set();

  function addPage(tabId, date, paragraphs, revision) {
    if (touched.has(tabId)) throw new Error('导出包含重复的日期页正文。');
    touched.add(tabId);
    const body = bodyText(paragraphs, date);
    if (body) pages.set(tabId, { tabId, date, body, sha256: hash(body), sourceRevision: revision });
    else pages.delete(tabId);
  }

  if (partial) {
    if (!snapshot.pages.length) throw new Error('连接器导出缺少日期页正文。');
    for (const page of snapshot.pages) {
      const item = tabs.get(page?.tabId);
      if (!item || page.documentId !== documentId || !Array.isArray(page.paragraphs)
        || page.paragraphs.some((line) => typeof line?.text !== 'string')) {
        throw new Error('连接器日期页与文档目录不一致。');
      }
      const pageRevision = revisionOf(page.revisionId);
      if (pageRevision && sourceRevision && pageRevision !== sourceRevision) {
        throw new Error('正文与目录来自不同文档修订，需重新导出。');
      }
      addPage(page.tabId, item.date, page.paragraphs.map((line) => line.text), pageRevision ?? sourceRevision);
    }
  } else {
    for (const [tabId, { date, tab }] of tabs) {
      const blocks = tab.documentTab?.body?.content;
      if (!Array.isArray(blocks)) throw new Error(`缺少 ${date} 正文，请使用包含页签正文的导出。`);
      addPage(tabId, date, nativeParagraphs(blocks), sourceRevision);
    }
  }

  const stats = { added: 0, updated: 0, unchanged: 0, removed: 0, retained: 0, total: pages.size };
  for (const tabId of touched) {
    const current = pages.get(tabId);
    if (!current) continue;
    const old = oldPages.get(tabId);
    if (!old) stats.added += 1;
    else if (old.sha256 === current.sha256 && old.date === current.date) stats.unchanged += 1;
    else stats.updated += 1;
  }
  for (const tabId of oldPages.keys()) {
    if (!pages.has(tabId)) stats.removed += 1;
    else if (!touched.has(tabId)) stats.retained += 1;
  }
  const ordered = Array.from(pages.values()).sort((left, right) =>
    right.date.localeCompare(left.date) || left.tabId.localeCompare(right.tabId));
  return { index: { version: 1, documentId, snapshotRevision: sourceRevision, pages: ordered }, stats };
}

export function searchHistory(index, { date, query, before, limit = 10 } = {}) {
  validateIndex(index);
  if ((date !== undefined && !validDate(date)) || (before !== undefined && !validDate(before))) {
    throw new Error('日期必须为有效的 YYYY-MM-DD。');
  }
  if (query !== undefined && (typeof query !== 'string' || !query.trim())) throw new Error('关键词不能为空。');
  if (!Number.isInteger(limit) || limit < 1 || limit > 50) throw new Error('结果数量须为 1 至 50。');
  const phrase = query?.trim() ?? '';
  const term = phrase ? new RegExp(phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'iu') : null;
  const results = [];
  for (const page of index.pages) {
    if ((date && page.date !== date) || (before && page.date >= before)) continue;
    const at = term ? page.body.search(term) : 0;
    if (at < 0) continue;
    const start = Math.max(0, at - 60);
    const end = Math.min(page.body.length, at + phrase.length + 160);
    const { tabId, sha256, sourceRevision } = page;
    const documentId = index.documentId;
    results.push({
      date: page.date, documentId, tabId, sha256, sourceRevision,
      matchesSnapshotRevision: sourceRevision && index.snapshotRevision ? sourceRevision === index.snapshotRevision : null,
      url: `https://docs.google.com/document/d/${documentId}/edit?tab=${encodeURIComponent(tabId)}`,
      excerpt: `${start ? '…' : ''}${page.body.slice(start, end)}${end < page.body.length ? '…' : ''}`,
    });
    if (results.length === limit) break;
  }
  return results;
}

function parseJson(text) {
  try {
    return JSON.parse(text.replace(/^\uFEFF/, ''));
  } catch {
    throw new Error('导出或历史索引不是有效的 JSON。');
  }
}

function readJson(path) {
  return parseJson(readFileSync(path, 'utf8'));
}

export function createDocsHistory({ text, schema, dateField, validateDate, stateDir }) {
  const indexPath = join(stateDir, 'history-index.json');
  return {
    definitions: [{
      name: 'search_docs_history',
      description: 'Search an authorized local Google Docs snapshot for literal text and date tabs. Results are historical excerpts and links, with cache age and per-tab revision. Check the original tab for current text.',
      inputSchema: schema({ query: text(200), date: dateField, before: dateField,
        limit: { type: 'integer', minimum: 1, maximum: 50 } }),
      annotations: { readOnlyHint: true, openWorldHint: false },
    }],
    handlers: {
      async search_docs_history(args) {
        if (args.date !== undefined) validateDate(args.date);
        if (args.before !== undefined) validateDate(args.before);
        const source_status = 'local_snapshot';
        try {
          if (!(await lstat(stateDir)).isDirectory() || !(await lstat(indexPath)).isFile()) {
            throw new Error('历史索引和状态目录须使用本地文件和目录。');
          }
          const file = await open(indexPath, 'r');
          try {
            const { mtime } = await file.stat();
            const results = searchHistory(parseJson(await file.readFile('utf8')), args);
            return { status: results.length ? 'ok' : 'no_results', source_status, updated_at: mtime.toISOString(), results };
          } finally {
            await file.close();
          }
        } catch (error) {
          if (error.code !== 'ENOENT') throw error;
          return { status: 'index_missing', source_status, results: [] };
        }
      },
    },
  };
}

function updateFile(snapshotPath, indexPath) {
  if (resolve(snapshotPath) === resolve(indexPath)) throw new Error('导出和索引须分别保存在不同文件。');
  const lock = `${indexPath}.lock`;
  const tmp = `${indexPath}.${randomUUID()}.tmp`;
  try {
    closeSync(openSync(lock, 'wx', 0o600));
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
    throw new Error('该历史索引正在更新，请稍后重试。');
  }
  try {
    let previous = null;
    try {
      previous = readJson(indexPath);
      validateIndex(previous);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    const { index, stats } = buildHistoryIndex(readJson(snapshotPath), previous);
    writeFileSync(tmp, `${JSON.stringify(index)}\n`, { mode: 0o600, flag: 'wx' });
    renameSync(tmp, indexPath);
    return stats;
  } finally {
    rmSync(lock, { force: true });
    rmSync(tmp, { force: true });
  }
}

function cli([command, ...args]) {
  if (command === 'update' && args.length === 2) return updateFile(...args);
  if (command === 'search') {
    const { positionals, values } = parseArgs({
      args, allowPositionals: true,
      options: Object.fromEntries(['date', 'query', 'before', 'limit'].map((name) => [name, { type: 'string' }])),
    });
    if (positionals.length === 1) {
      const { limit, ...filters } = values;
      return searchHistory(readJson(positionals[0]), { ...filters, limit: limit === undefined ? 10 : Number(limit) });
    }
  }
  throw new Error('用法：docs-history.mjs update SNAPSHOT.json INDEX.json 或 search INDEX.json [--date YYYY-MM-DD] [--query 关键词] [--before YYYY-MM-DD] [--limit 2]');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    console.log(JSON.stringify(cli(process.argv.slice(2)), null, 2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 2;
  }
}
