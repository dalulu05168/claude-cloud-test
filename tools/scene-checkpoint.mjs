import { getBlocks, pictureFooter } from './speech-format.mjs';
import { createRevisionStore, latestSnapshotIds, revisionField } from './state-revisions.mjs';

const choice = (values) => ({ type: 'string', enum: values, maxLength: 30 });
export const sceneSession = choice(['morning', 'afternoon', 'evening']);
const mobileSessions = { 上午: 'morning', 下午: 'afternoon', 晚间: 'evening' };
const timeZone = 'Europe/Bucharest';
const snapshotPattern = /^scene-(\d{4}-\d{2}-\d{2})-(morning|afternoon|evening)-(\d{6})$/;
const bodyLimit = 120000;
const snapshotLimit = 1150000;
const resumeLineLimit = 1000;

function inspectBody(body, includeTail = true) {
  const blocks = getBlocks(body);
  if (!blocks.length || blocks.some((block) => !block.lines.some((line) => line.trim()))) {
    throw new Error('A scene checkpoint needs labelled messages with nonempty text.');
  }
  const lines = body.split(/\r\n|\r|\n/);
  const lastBlock = blocks.at(-1);
  const lastSpokenLine = lines.slice(lastBlock.line - 1).findLast((line, index) => {
    const trimmed = line.trim();
    return trimmed && !pictureFooter.test(trimmed) && !(index === 0 && trimmed === lastBlock.label);
  });
  return {
    confirmed_count: blocks.length,
    last_speaker: lastBlock.label,
    last_line: lines.findLast((line) => line.trim()),
    last_spoken_line: (lastSpokenLine === lines[lastBlock.line - 1] ? lastBlock.inlineStaffText : undefined)
      ?? lastSpokenLine ?? null,
    ...(includeTail ? {
      tail: lines.slice(blocks.at(-Math.min(3, blocks.length)).line - 1).join('\n').trimEnd().slice(-4000),
    } : {}),
  };
}

function trackView(track, includeBody) {
  if (!track) return null;
  const { body, included_adopted_ids, ...details } = track;
  const transcript = inspectBody(body);
  return { ...details, ...transcript,
    completed_count: details.coverage === 'from_start' ? transcript.confirmed_count : null,
    ...(includeBody ? { body } : {}) };
}

function resumeTrack(track) {
  if (!track) return null;
  const { last_speaker, last_spoken_line, confirmed_count } = inspectBody(track.body, false);
  const line = last_spoken_line === null ? null : Array.from(last_spoken_line);
  const truncated = line !== null && line.length > resumeLineLimit;
  return {
    status: track.status,
    node: track.node,
    stop_after: track.stop_after,
    next_step: track.next_step,
    unresolved: track.unresolved,
    last_speaker,
    last_spoken_line: truncated ? line.slice(0, resumeLineLimit).join('') : last_spoken_line,
    last_spoken_line_truncated: truncated,
    completed_count: track.coverage === 'from_start' ? confirmed_count : null,
  };
}

export function createSceneCheckpointTools({ text, schema, dateField, validateDate, listIds, readEntry, insertRecord, getRecord }) {
  const { readSnapshot, saveSnapshot } = createRevisionStore({ listIds, readEntry, insertRecord });
  const dateAndSession = { date: dateField, session: sceneSession };
  const definitions = [
    {
      name: 'get_scene_checkpoint',
      description: 'Restore one Bucharest scene by date and session. Returns the exact last line, cumulative message count, waiting node, unresolved items, and source for adopted and actually sent text separately.',
      inputSchema: schema({ ...dateAndSession, include_body: { type: 'boolean' } }, ['date', 'session']),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    {
      name: 'get_resume_context',
      description: 'Find saved Westcott scene progress across sessions. Pass no arguments for the latest saved Bucharest scene date. Optionally filter by date or session. Returns a compact checkpoint for one match or candidate scenes to choose from. Use when asked to pick up where we left off, 接上次进度, or 只看上次停点.',
      inputSchema: schema(dateAndSession),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    {
      name: 'save_scene_checkpoint',
      description: 'Save user-confirmed adopted text or explicitly confirmed sent text for one scene. Use replace for the full verified scene, append for new confirmed labelled blocks only. Does not send messages or advance lesson tasks.',
      inputSchema: schema({
        ...dateAndSession, expected_revision: revisionField, track: choice(['adopted', 'sent']),
        mode: choice(['replace', 'append']), coverage: choice(['from_start', 'partial']),
        body: text(bodyLimit), adopted_record_id: text(44),
        status: choice(['in_progress', 'waiting', 'closed']), node: text(300),
        stop_after: text(1000), next_step: text(1000),
        unresolved: { type: 'array', items: text(500), minItems: 0, maxItems: 20 },
        source: schema({ ref: text(1000), tab: text(300), revision: text(300) }, ['ref']),
        confirmation: text(1000),
      }, ['date', 'session', 'expected_revision', 'track', 'status', 'node', 'unresolved', 'source', 'confirmation']),
      annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
    },
  ];

  async function latest(date, session) {
    validateDate(date);
    const [id] = latestSnapshotIds(await listIds(), snapshotPattern, ([, day, slot]) => day === date && slot === session);
    return id ? readSnapshot(id, 'scene_checkpoint') : { date, session, revision: 0, adopted: null, sent: null };
  }

  function view({ date, session, revision, adopted, sent }, includeBody = false) {
    return { date, session, timezone: timeZone, revision,
      adopted: trackView(adopted, includeBody), sent: trackView(sent, includeBody) };
  }

  const handlers = {
    async get_scene_checkpoint({ date, session, include_body }) {
      return view(await latest(date, session), include_body);
    },
    async get_resume_context({ date, session }) {
      if (date) validateDate(date);
      const ids = latestSnapshotIds(await listIds(), snapshotPattern,
        ([, day, slot]) => (!date || day === date) && (!session || slot === session));
      if (!ids.length) return { status: 'empty', timezone: timeZone };

      const latestDate = date ?? snapshotPattern.exec(ids.sort().at(-1))[1];
      const scenes = await Promise.all(ids.filter((id) => snapshotPattern.exec(id)[1] === latestDate)
        .map((id) => readSnapshot(id, 'scene_checkpoint')));
      if (scenes.length > 1) {
        scenes.sort((a, b) => sceneSession.enum.indexOf(a.session) - sceneSession.enum.indexOf(b.session));
        return { status: 'choose', timezone: timeZone,
          candidates: scenes.map(({ date, session, revision, adopted, sent }) => ({
            date, session, revision, adopted_status: adopted?.status ?? null, sent_status: sent?.status ?? null,
          })) };
      }
      const [scene] = scenes;
      return {
        status: 'found',
        timezone: timeZone,
        checkpoint: {
          date: scene.date,
          session: scene.session,
          revision: scene.revision,
          adopted: resumeTrack(scene.adopted),
          sent: resumeTrack(scene.sent),
        },
      };
    },
    async save_scene_checkpoint({ date, session, expected_revision, track, mode = 'replace',
      coverage, body, adopted_record_id, status, node, stop_after, next_step, unresolved, source, confirmation }) {
      const current = await latest(date, session);
      if (current.revision !== expected_revision) throw new Error('Revision changed. Read the latest scene checkpoint first.');
      if (status === 'waiting' && !stop_after) throw new Error('A waiting scene needs an explicit stopping boundary.');
      if (body && adopted_record_id) throw new Error('Use either confirmed text or an adopted record ID.');
      if (adopted_record_id) {
        const record = await getRecord(adopted_record_id);
        const recordSession = record.session ?? mobileSessions[record.mobile_handoff?.session];
        if (record.kind !== 'adopted' || record.date !== date || (recordSession && recordSession !== session)) {
          throw new Error('Use an adopted record from the same date and scene.');
        }
        body = record.body;
      }
      const previous = current[track];
      if (mode === 'append' && (!previous || !body)) {
        throw new Error('Appending requires an existing checkpoint on this track and new confirmed text.');
      }
      if (!body && !previous) throw new Error('The first checkpoint on this track needs confirmed text.');
      const sameSource = previous?.source.ref === source.ref && previous.source.tab === source.tab;
      if (sameSource && (mode === 'append' || !body)) {
        if (source.revision && source.revision !== previous.source.revision) {
          throw new Error('The source revision changed. Replace the checkpoint with the verified current transcript.');
        }
        source = { ...source, revision: previous.source.revision };
      }
      if ((mode === 'append' || !body) && coverage && coverage !== previous.coverage) {
        throw new Error('To change transcript coverage, replace it with verified text.');
      }
      const transcriptCoverage = mode === 'append' || !body ? previous.coverage : (coverage ?? 'partial');
      const knownIds = previous?.included_adopted_ids ?? (previous?.adopted_record_id ? [previous.adopted_record_id] : []);
      if (mode === 'append' && adopted_record_id && knownIds.includes(adopted_record_id)) {
        return { ...view(current), unchanged: true };
      }
      const includedAdoptedIds = new Set(knownIds);
      if (adopted_record_id) includedAdoptedIds.add(adopted_record_id);
      if (body) inspectBody(body);
      else adopted_record_id = previous.adopted_record_id;
      if (mode === 'append') body = `${previous.body}\n\n${body}`;
      body ??= previous.body;
      if (body.length > bodyLimit) throw new Error('The confirmed scene transcript is too long.');
      const snapshot = { date, session, adopted: current.adopted, sent: current.sent,
        [track]: { body, coverage: transcriptCoverage, status, node, stop_after, next_step,
          unresolved, source, confirmation, adopted_record_id, included_adopted_ids: [...includedAdoptedIds] } };
      if (Buffer.byteLength(JSON.stringify(snapshot, null, 2)) > snapshotLimit) {
        throw new Error('The scene checkpoint is too large to store.');
      }
      return view(await saveSnapshot(`scene-${date}-${session}`, 'scene_checkpoint', expected_revision, snapshot));
    },
  };
  return { definitions, handlers };
}
