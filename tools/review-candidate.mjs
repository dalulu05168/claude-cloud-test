import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, rmdir, unlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const bundle = fileURLToPath(new URL('../reference/base-workflow/', import.meta.url));
const reviewScript = join(bundle, 'scripts', 'review_draft.py');
const digest = (value) => createHash('sha256').update(value).digest('hex');

export function createCandidateReview({ schema, text, dateField, validateDate }) {
  return {
    definitions: [{
      name: 'review_candidate_draft',
      description: 'Run the pinned westcott-efficient-workflow review_draft.py on supplied draft text. Returns a structural report and exact draft/rules hashes. Does not verify facts, judge topic selection, or change project records.',
      inputSchema: schema({
        body: text(160000), date: dateField,
        scope: { ...text(7), enum: ['auto', 'evening'] },
        fragment: { type: 'boolean' }, topic: text(300), times: text(16000),
      }, ['body', 'date']),
      annotations: { readOnlyHint: true, openWorldHint: false },
    }],
    handlers: {
      async review_candidate_draft({ body, date, scope = 'auto', fragment = false, topic, times }) {
        validateDate(date);
        const python = process.env.WESTCOTT_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
        const folder = await mkdtemp(join(tmpdir(), 'westcott-review-'));
        const draftPath = join(folder, 'draft.txt');
        try {
          await writeFile(draftPath, body, { encoding: 'utf8', mode: 0o600, flag: 'wx' });
          const args = ['-B', '-I', reviewScript, draftPath, '--date', date, '--scope', scope, '--json'];
          if (fragment) args.push('--fragment');
          if (topic) args.push(`--topic=${topic}`);
          if (times) args.push(`--times=${times}`);
          const run = spawnSync(python, args, {
            encoding: 'utf8', timeout: 10000, maxBuffer: 1024 * 1024, windowsHide: true,
          });
          if (run.error || ![0, 1, 2].includes(run.status) || !run.stdout?.trim()) {
            throw new Error('review_draft.py could not run. Check the Python 3.9+ executable and the pinned skill.');
          }
          const report = JSON.parse(run.stdout);
          if (!['PASS', 'FAIL', 'INPUT_ERROR'].includes(report.status)) {
            throw new Error('review_draft.py returned an unexpected status.');
          }
          return { report, draft_sha256: digest(body), rules_manifest_sha256: digest(await readFile(join(bundle, 'manifest.json'))) };
        } finally {
          await unlink(draftPath).catch((error) => { if (error.code !== 'ENOENT') throw error; });
          await rmdir(folder);
        }
      },
    },
  };
}
