import { createHash } from 'node:crypto';
import { lstat, readFile } from 'node:fs/promises';
import { join } from 'node:path';

const jsonLimit = 128000;
const phases = new Map([
  ['drafted:pending_review', 'pending_review'],
  ['drafted:blocked', 'blocked'],
  ['ready_for_docs:passed', 'ready_for_human_review'],
]);
const isText = (value, limit) => typeof value === 'string' && value.length <= limit && Boolean(value.trim());

async function readBounded(path, limit) {
  try {
    const info = await lstat(path);
    if (!info.isFile() || info.size < 1 || info.size > limit) throw new Error('Invalid daily run file.');
    const data = await readFile(path);
    if (!data.length || data.length > limit) throw new Error('Invalid daily run file.');
    return data;
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

async function readOptionalRecord(run, name) {
  try {
    const data = await readBounded(join(run, name), jsonLimit);
    return data ? JSON.parse(data.toString('utf8')) : null;
  } catch {
    return null;
  }
}

async function checkArtifact(run, artifact, name, limit) {
  const data = await readBounded(join(run, name), limit);
  if (!data || artifact?.path !== name || artifact.size_bytes !== data.length) {
    throw new Error('Daily run file does not match the manifest.');
  }
  const digest = createHash('sha256').update(data).digest('hex');
  if (artifact.sha256 !== digest) throw new Error('Daily run file does not match the manifest.');
  return digest;
}

function validatedReview(record, identity, version) {
  if (record?.schema_version !== 'westcott-codex-review-v1' || record.run_id !== identity.run_id
    || record.task_date !== identity.date || record.ruleset_version !== identity.ruleset_version
    || record.draft_sha256 !== version.draft_sha256 || record.research_sha256 !== version.research_sha256) return null;
  const { status, summary, issues } = record.review ?? {};
  if (!['ready_for_human_review', 'needs_evidence'].includes(status) || !isText(summary, 1000)
    || !Array.isArray(issues) || issues.length > 30 || issues.some((issue) => !isText(issue, 1000))
    || (status === 'needs_evidence') !== Boolean(issues.length)) return null;
  return { status, summary, issues };
}

function validatedDocs(record, runId, version) {
  if (record?.run_id !== runId || record.status !== 'saved'
    || record.content_sha256 !== version.draft_sha256
    || typeof record.document_id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(record.document_id)
    || !isText(record.url, 2000)) return null;
  const path = '/document/d/' + record.document_id;
  try {
    const url = new URL(record.url);
    if (url.origin !== 'https://docs.google.com' || url.username || url.password
      || ![path, path + '/edit', path + '/view'].includes(url.pathname)) return null;
    return { status: 'saved', document_id: record.document_id, url: url.origin + path + '/edit' };
  } catch {
    return null;
  }
}

export function createDailyRunStatus({ schema, dateField, validateDate, state }) {
  return {
    definitions: [{
      name: 'get_daily_run_status',
      description: 'Read one dated daily automation run and its current artifact hashes. Shows matching Codex findings and Google Docs save receipt when present. Candidate status does not record adoption, human approval or external sending.',
      inputSchema: schema({ date: dateField }, ['date']),
      annotations: { readOnlyHint: true, openWorldHint: false },
    }],
    handlers: {
      async get_daily_run_status({ date }) {
        validateDate(date);
        const run_id = 'daily-' + date;
        const identity = { date, run_id };
        const runs = join(state, 'runs');
        const run = join(runs, run_id);
        try {
          for (const folder of [state, runs, run]) {
            if (!(await lstat(folder)).isDirectory()) throw new Error('Invalid daily run directory.');
          }
        } catch (error) {
          return { ...identity, status: error.code === 'ENOENT' ? 'missing' : 'invalid' };
        }

        try {
          const raw = await readBounded(join(run, 'manifest.json'), jsonLimit);
          if (!raw) return { ...identity, status: 'missing' };
          const manifest = JSON.parse(raw.toString('utf8'));
          const { ruleset_version, updated_at, validation, artifacts } = manifest ?? {};
          const phase = phases.get(manifest?.status + ':' + validation?.status);
          if (!phase || manifest.protocol_version !== '1.0' || manifest.run_id !== run_id || manifest.task_date !== date
            || typeof ruleset_version !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$/.test(ruleset_version)) {
            return { ...identity, status: 'invalid' };
          }
          const [draft_sha256, research_sha256, review, docs] = await Promise.all([
            checkArtifact(run, artifacts?.draft, 'draft.md', 200000),
            checkArtifact(run, artifacts?.research, 'research.json', jsonLimit * 4),
            readOptionalRecord(run, 'codex-review.json'),
            readOptionalRecord(run, 'google-docs.json'),
          ]);
          const version = { draft_sha256, research_sha256 };
          const codex_review = validatedReview(review, { ...identity, ruleset_version }, version);
          const google_docs = validatedDocs(docs, run_id, version);
          return {
            ...identity, status: codex_review?.status === 'needs_evidence' ? 'blocked' : phase, ruleset_version, version,
            ...(isText(updated_at, 35) && Number.isFinite(Date.parse(updated_at)) ? { updated_at } : {}),
            ...(codex_review ? { codex_review } : {}),
            ...(google_docs ? { google_docs } : {}),
          };
        } catch {
          return { ...identity, status: 'invalid' };
        }
      },
    },
  };
}
