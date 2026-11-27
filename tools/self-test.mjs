import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const project = mkdtempSync(join(tmpdir(), 'westcott-mcp-'));
const server = fileURLToPath(new URL('./course-state.mjs', import.meta.url));
const request = (id, method, params) => ({ jsonrpc: '2.0', id, method, params });
const call = (id, name, args) => request(id, 'tools/call', { name, arguments: args });

function run(messages) {
  const run = spawnSync(process.execPath, [server], {
    env: { ...process.env, CLAUDE_PROJECT_DIR: project },
    input: messages.map((message) => JSON.stringify(message)).join('\n') + '\n',
    encoding: 'utf8',
    timeout: 5000,
  });
  assert.equal(run.status, 0, run.stderr || String(run.error || 'Process failed'));
  assert.equal(run.stderr, '');
  return run.stdout.trim().split('\n').map((line) => JSON.parse(line));
}

function decode(response) {
  assert.equal(response.result.isError, undefined, response.result.content?.[0]?.text);
  return JSON.parse(response.result.content[0].text);
}

try {
  const stateDirectory = join(project, '.westcott-state');
  const [emptyResume] = run([call(1, 'get_resume_context', {})]);
  assert.deepEqual(decode(emptyResume), { status: 'empty', timezone: 'Europe/Bucharest' });
  assert.equal(existsSync(stateDirectory), false);
  const python = process.env.WESTCOTT_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  if (process.env.WESTCOTT_PYTHON || spawnSync(python, ['--version'], { timeout: 2000 }).status === 0) {
    const reviewInput = { date: '2026-11-27', fragment: true };
    const body = '===== 资讯助理 =====\n\n🌤 大家下午好！欢迎回到 ELLERY WESTCOTT 社群！\n\n12男：\nPăi，我觉得这个要再看看。\n';
    const [daytimeReview, eveningReview] = run([
      call(1, 'review_candidate_draft', { ...reviewInput, body }),
      call(2, 'review_candidate_draft', { ...reviewInput, scope: 'evening', body: body.replace('下午', '晚上') }),
    ]).map(decode);
    assert.equal(daytimeReview.report.status, 'PASS');
    assert.equal(eveningReview.report.status, 'FAIL');
    assert.match(JSON.stringify(eveningReview.report), /罗语口头词/);
    assert.equal(daytimeReview.draft_sha256, createHash('sha256').update(body).digest('hex'));
    assert.match(daytimeReview.rules_manifest_sha256, /^[a-f0-9]{64}$/);
    assert.equal(existsSync(stateDirectory), false);
  } else {
    console.log('review_candidate_draft execution skipped: set WESTCOTT_PYTHON to a Python 3.9+ executable.');
  }
  const draftText = { date: '2026-11-17', topic: '箱体', growth_step: '观察机构线索',
    next_question: '为什么要跟踪机构？', body: '教学模拟：先看价量。', source: '模拟课件' };
  const results = run([
    request(1, 'initialize', { protocolVersion: '2025-06-18' }),
    { jsonrpc: '2.0', method: 'notifications/initialized' },
    request(2, 'tools/list'),
    call(3, 'list_records', {}),
    call(4, 'save_draft', draftText),
    call(5, 'list_records', {}),
    call(6, 'save_draft', { ...draftText, date: '2026-02-30' }),
    call(7, 'read_record', { id: '../CLAUDE.md' }),
  ]);
  assert.equal(results.length, 7);
  assert.equal(results[0].result.protocolVersion, '2025-06-18');
  assert.equal(results[0].result.serverInfo.version, '1.7.3');
  assert.deepEqual(results[1].result.tools.map((tool) => tool.name),
    ['list_records', 'read_record', 'get_agenda', 'save_agenda', 'save_draft', 'record_adoption',
      'complete_lesson', 'get_scene_checkpoint', 'get_resume_context', 'save_scene_checkpoint',
      'get_handoff', 'save_handoff', 'get_fact_checks', 'save_fact_check',
      'preview_mobile_handoff', 'import_mobile_handoff',
      'get_cast_context', 'save_member_profile', 'save_member_activity', 'build_lesson_kit', 'validate_speech_format',
      'validate_whatsapp_voice', 'validate_news_broadcast', 'validate_scene_count',
      'validate_local_edit', 'plan_market_open', 'review_candidate_draft', 'get_daily_run_status',
      'search_docs_history']);
  assert.equal(results[1].result.tools.find((tool) => tool.name === 'review_candidate_draft').annotations.readOnlyHint, true);
  assert.equal(results[1].result.tools.find((tool) => tool.name === 'get_resume_context').annotations.readOnlyHint, true);
  assert.deepEqual(decode(results[2]).records, []);
  const { id } = decode(results[3]);
  assert.deepEqual(decode(results[4]).records, []);
  assert.equal(results[5].result.isError, true);
  assert.equal(results[6].result.isError, true);

  const adoption = run([
    call(1, 'read_record', { id }),
    call(2, 'record_adoption', { id, approval: '我采用这版。' }),
    call(3, 'record_adoption', { id, approval: '再次提交同一版。' }),
    call(4, 'list_records', {}),
  ]);
  assert.equal(decode(adoption[0]).body, draftText.body);
  const adopted = decode(adoption[1]);
  assert.deepEqual(decode(adoption[2]), adopted);
  assert.equal(decode(adoption[3]).records.length, 1);
  const [first, second, third] = run([
    call(1, 'read_record', { id: adopted.id }),
    call(2, 'read_record', { id }),
    call(3, 'list_records', { kind: 'all' }),
  ]);
  assert.equal(decode(first).approval, '我采用这版。');
  assert.equal(decode(first).next_question, draftText.next_question);
  assert.equal(decode(second).kind, 'draft');
  assert.equal(decode(third).records.length, 2);
  const phase = { id: 'lesson-trial', title: '测试用阶段', goal: '从识别机构线索到理解观察目的' };
  const formation = { id: 'chart-shape', course: '形态课', goal: '讲回踩时同步带出观察机构交易线索',
    leave_for_later: '为什么要跟踪机构，留给下一场投资理念课', scheduled_date: '2026-11-18' };
  const philosophy = { id: 'why-observe', course: '投资理念课', goal: '回答为什么要跟踪机构',
    leave_for_later: '具体用哪些资料追踪，后续安排确定后再讲', scheduled_date: '2026-11-19' };
  const agenda = { expected_revision: 0, phase, source: '测试任务线', tasks: [formation, philosophy] };
  const [beforePlan, badOrder, plan, stale] = run([
    call(1, 'get_agenda', {}),
    call(2, 'save_agenda', { ...agenda, tasks: [philosophy, formation] }),
    call(3, 'save_agenda', agenda),
    call(4, 'save_agenda', agenda),
  ]);
  assert.equal(decode(beforePlan).latest_adopted_lesson, null);
  assert.equal(decode(beforePlan).next_task, null);
  assert.equal(badOrder.result.isError, true);
  assert.equal(decode(plan).revision, 1);
  assert.equal(decode(plan).next_task.id, formation.id);
  assert.equal(stale.result.isError, true);

  const linked = (task) => ({ ...draftText, date: task.scheduled_date, topic: task.course,
    growth_step: task.goal, phase_id: phase.id, task_id: task.id });
  const fifthCandleLine = '第五根收回来以后，先看它能不能站得住。';
  const eveningScene = { date: formation.scheduled_date, session: 'evening' };
  const [badDate, secondDraft, firstDraft, completeFirstDraft, afterDraft] = run([
    call(1, 'save_draft', { ...linked(formation), date: philosophy.scheduled_date }),
    call(2, 'save_draft', linked(philosophy)),
    call(3, 'save_draft', { ...linked(formation), ...eveningScene,
      body: '===== David先生 =====\n\n' + fifthCandleLine }),
    call(4, 'save_draft', { ...linked(formation), lesson_scope: 'full_lesson' }),
    call(5, 'get_agenda', {}),
  ]);
  assert.equal(badDate.result.isError, true);
  assert.equal(decode(afterDraft).next_task.id, formation.id);
  const [tooEarly, adoptSection, afterSection] = run([
    call(1, 'record_adoption', { id: decode(secondDraft).id, approval: '测试采用。' }),
    call(2, 'record_adoption', { id: decode(firstDraft).id, approval: '测试采用。' }),
    call(3, 'get_agenda', {}),
  ]);
  assert.equal(tooEarly.result.isError, true);
  assert.equal(decode(afterSection).next_task.id, formation.id);
  assert.equal(decode(afterSection).latest_adopted_lesson, null);
  const [savedCandle] = run([call(1, 'save_scene_checkpoint', { ...eveningScene, expected_revision: 0,
    track: 'adopted', adopted_record_id: decode(adoptSection).id, status: 'in_progress', node: '课堂读图中段',
    unresolved: [draftText.next_question], source: { ref: '模拟科目' }, confirmation: '只确认第五根 K 线的片段。' })]);
  assert.equal(decode(savedCandle).adopted.last_spoken_line, fifthCandleLine);
  const [resumedCandle, cursorAgenda] = run([
    call(1, 'get_scene_checkpoint', eveningScene),
    call(2, 'get_agenda', {}),
  ]);
  assert.equal(decode(resumedCandle).adopted.last_spoken_line, fifthCandleLine);
  assert.deepEqual(decode(resumedCandle).adopted.unresolved, [draftText.next_question]);
  assert.equal(decode(cursorAgenda).next_task.id, formation.id);
  const [invalidCompletion, adoptFirst, beforeCompletion] = run([
    call(1, 'complete_lesson', { id: decode(adoptSection).id, confirmation: '模拟误把中段当作整课。' }),
    call(2, 'record_adoption', { id: decode(completeFirstDraft).id, approval: '测试采用完整稿。' }),
    call(3, 'get_agenda', {}),
  ]);
  assert.match(invalidCompletion.result.content[0].text, /full_lesson/);
  assert.equal(decode(beforeCompletion).next_task.id, formation.id);
  const finishFirst = { id: decode(adoptFirst).id, confirmation: '模拟用户确认第一课完整结束。' };
  const [completion, repeatedCompletion, next] = run([
    call(1, 'complete_lesson', finishFirst),
    call(2, 'complete_lesson', finishFirst),
    call(3, 'get_agenda', {}),
  ]);
  assert.equal(decode(completion).status, 'completed');
  assert.deepEqual(decode(repeatedCompletion), decode(completion));
  assert.equal(decode(next).tasks[0].adopted_record_id, decode(adoptFirst).id);
  assert.equal(decode(next).latest_adopted_lesson.id, decode(adoptFirst).id);
  assert.equal(decode(next).next_task.goal, '回答为什么要跟踪机构');
  assert.equal(decode(next).phase.id, phase.id);
  const [futureCheckpoint, futureAgenda] = run([
    call(1, 'get_scene_checkpoint', { date: philosophy.scheduled_date, session: 'evening' }),
    call(2, 'get_agenda', {}),
  ]);
  assert.equal(decode(futureCheckpoint).adopted, null);
  assert.equal(decode(futureAgenda).next_task.id, philosophy.id);

  const revisedPhilosophy = { ...philosophy, leave_for_later: '跟踪资料留到下一阶段' };
  const [planEdit, staleDraft, replacement] = run([
    call(1, 'save_agenda', { ...agenda, expected_revision: 1, tasks: [formation, revisedPhilosophy] }),
    call(2, 'record_adoption', { id: decode(secondDraft).id, approval: '测试采用。' }),
    call(3, 'save_draft', { ...linked(revisedPhilosophy), lesson_scope: 'full_lesson' }),
  ]);
  assert.equal(decode(planEdit).tasks[0].status, 'adopted');
  assert.equal(staleDraft.result.isError, true);
  const [adoptSecond, oldRecord] = run([
    call(1, 'record_adoption', { id: decode(replacement).id, approval: '测试采用。' }),
    call(2, 'read_record', { id: adopted.id }),
  ]);
  const [, done] = run([
    call(1, 'complete_lesson', { id: decode(adoptSecond).id, confirmation: '模拟用户确认第二课完成。' }),
    call(2, 'get_agenda', {}),
  ]);
  assert.equal(decode(done).tasks[1].adopted_record_id, decode(adoptSecond).id);
  assert.equal(decode(done).next_task, null);
  assert.equal(decode(done).phase.id, phase.id);
  assert.equal(decode(oldRecord).body, draftText.body);
  const plain = '有些人会先看看 BVB 的 1,25% 变化。';
  const beforeEdit = '【教学模拟】\r\n\r\n14男：我先看收盘。\r\n\r\n12男：' + plain + '\r\n\r\n37女：等确认了再看吧。';
  const revised = beforeEdit.replace(plain, '我想先看 BVB 的 1,25% 是怎么变的。');
  const edit = { original: beforeEdit, revised, editable_blocks: [2], protected_literals: ['BVB'] };
  const [precise, numberChanged, otherChanged, roleChanged, addedLine, plainEdit, deliberate, lostLiteral, invalidTarget] = run([
    call(1, 'validate_local_edit', edit),
    call(2, 'validate_local_edit', { ...edit, revised: revised.replace('1,25%', '2,15%') }),
    call(3, 'validate_local_edit', { ...edit, revised: revised.replace('等确认了再看吧。', '继续。') }),
    call(4, 'validate_local_edit', { ...edit, revised: revised.replace('12男：', '18男：') }),
    call(5, 'validate_local_edit', { ...edit, revised: revised + '\r\n\r\n37女：新增。' }),
    call(6, 'validate_local_edit', { original: plain, revised: '我先看 BVB 的 1,25%。' }),
    call(7, 'validate_local_edit', { ...edit, revised: revised.replace('1,25%', '2,15%'), allow_number_changes: true }),
    call(8, 'validate_local_edit', { ...edit, revised: revised.replace('BVB', 'BET') }),
    call(9, 'validate_local_edit', { ...edit, editable_blocks: [4] }),
  ]);
  for (const item of [precise, plainEdit, deliberate]) assert.equal(decode(item).passed, true);
  for (const [item, type] of [[numberChanged, 'number'], [otherChanged, 'outside_scope'], [roleChanged, 'role'],
    [addedLine, 'block_count'], [lostLiteral, 'literal'], [invalidTarget, 'scope']]) {
    assert.equal(decode(item).passed, false);
    assert.ok(decode(item).issues.some((issue) => issue.type === type));
  }
  const longBody = '教学示例'.repeat(1000) + '收盘才核对。';
  const [savedLong] = run([call(1, 'save_draft', { ...draftText, body: longBody })]);
  const [boundedRead] = run([call(1, 'read_record', { id: decode(savedLong).id, view: 'tail' })]);
  assert.equal(decode(boundedRead).body, undefined);
  assert.equal(decode(boundedRead).body_tail, longBody.slice(-3000));
  assert.equal(decode(boundedRead).more_before, true);
  const date = '2026-11-19';
  const scene = { date, session: 'afternoon' };
  const opening = '===== 资讯助理 =====\r\n\r\n下午好。';
  const waitingLine = '我先等那份材料。';
  const waitingBody = `【教学模拟】\r\n\r\n${opening}\r\n\r\n14男：\r\n${waitingLine}\r\n`;
  const checkpoint = { ...scene, expected_revision: 0, track: 'adopted', coverage: 'from_start', body: waitingBody,
    status: 'waiting', node: '等待美股推票材料', stop_after: '材料到之前停在此处',
    next_step: '材料到了再核对代码', unresolved: ['材料还没到'],
    source: { ref: '模拟正式文档', tab: '11 月 19 日下午', revision: 'r1' }, confirmation: '模拟用户确认采用以上两条。' };
  const [emptyScene, initialCheckpoint] = run([
    call(1, 'get_scene_checkpoint', scene),
    call(2, 'save_scene_checkpoint', checkpoint),
  ]);
  assert.equal(decode(emptyScene).revision, 0);
  assert.equal(decode(initialCheckpoint).revision, 1);
  assert.equal(decode(initialCheckpoint).sent, null);
  const [newWindow, sentCheckpoint, staleScene] = run([
    call(1, 'get_scene_checkpoint', scene),
    call(2, 'save_scene_checkpoint', { ...checkpoint, expected_revision: 1, track: 'sent', body: opening,
      status: 'in_progress', node: '已经开群', confirmation: '模拟用户只确认开场已实际发送。' }),
    call(3, 'save_scene_checkpoint', { ...checkpoint, expected_revision: 1 }),
  ]);
  const restored = decode(newWindow);
  assert.equal(restored.adopted.last_line, waitingLine);
  assert.equal(restored.adopted.last_speaker, '14男：');
  assert.equal(restored.adopted.completed_count, 2);
  assert.equal(restored.adopted.body, undefined);
  assert.equal(restored.adopted.node, '等待美股推票材料');
  assert.equal(restored.adopted.status, 'waiting');
  assert.equal(restored.adopted.stop_after, checkpoint.stop_after);
  assert.equal(restored.adopted.next_step, checkpoint.next_step);
  assert.deepEqual(restored.adopted.unresolved, checkpoint.unresolved);
  assert.deepEqual(restored.adopted.source, checkpoint.source);
  assert.equal(decode(sentCheckpoint).sent.completed_count, 1);
  assert.match(staleScene.result.content[0].text, /Revision changed/);
  const followOn = '12男：今天先不猜，我等确认。';
  const [malformedScene, appendedScene, reopenedScene, separateScene] = run([
    call(1, 'save_scene_checkpoint', { ...checkpoint, expected_revision: 2, body: '12男：' }),
    call(2, 'save_scene_checkpoint', { ...checkpoint, expected_revision: 2, mode: 'append', body: followOn }),
    call(3, 'get_scene_checkpoint', { ...scene, include_body: true }),
    call(4, 'get_scene_checkpoint', { ...scene, session: 'evening' }),
  ]);
  assert.equal(malformedScene.result.isError, true);
  assert.equal(decode(appendedScene).adopted.completed_count, 3);
  assert.equal(decode(reopenedScene).adopted.body, `${waitingBody}\n\n${followOn}`);
  assert.equal(decode(reopenedScene).sent.body, opening);
  assert.equal(decode(separateScene).revision, 0);
  const replacementSource = { ...checkpoint.source, revision: 'r2' };
  const replacementBody = waitingBody.replace(waitingLine, '我等新的材料。');
  const [stalePrefix, relabelledRev, replaced, staleAfterRebase] = run([
    call(1, 'save_scene_checkpoint', { ...checkpoint, expected_revision: 3, mode: 'append',
      body: followOn, source: replacementSource }),
    call(2, 'save_scene_checkpoint', { ...checkpoint, expected_revision: 3, body: undefined, source: replacementSource }),
    call(3, 'save_scene_checkpoint', { ...checkpoint, expected_revision: 3, body: replacementBody, source: replacementSource }),
    call(4, 'save_scene_checkpoint', { ...checkpoint, expected_revision: 4, mode: 'append', body: followOn }),
  ]);
  for (const result of [stalePrefix, relabelledRev, staleAfterRebase]) {
    assert.equal(result.result.isError, true);
    assert.match(result.result.content[0].text, /source revision changed/);
  }
  assert.equal(decode(replaced).adopted.completed_count, 2);
  assert.equal(decode(replaced).adopted.last_spoken_line, '我等新的材料。');
  assert.equal(decode(replaced).adopted.source.revision, replacementSource.revision);
  const markedSnippet = '37女：等那条正式消息再说。';
  const [snippetDraft] = run([call(1, 'save_draft', { ...draftText, ...scene, body: markedSnippet })]);
  const [snippetAdoption] = run([call(1, 'record_adoption',
    { id: decode(snippetDraft).id, approval: '模拟确认这条可以追加。' })]);
  const appendAdopted = { ...checkpoint, source: replacementSource, body: undefined,
    mode: 'append', adopted_record_id: decode(snippetAdoption).id };
  const [appendedOnce] = run([call(1, 'save_scene_checkpoint', { ...appendAdopted, expected_revision: 4 })]);
  const [retried, nextAppend, retryAfterMoreText] = run([
    call(1, 'save_scene_checkpoint', { ...appendAdopted, expected_revision: 5 }),
    call(2, 'save_scene_checkpoint', { ...checkpoint, source: replacementSource,
      expected_revision: 5, mode: 'append', body: followOn }),
    call(3, 'save_scene_checkpoint', { ...appendAdopted, expected_revision: 6 }),
  ]);
  assert.equal(decode(appendedOnce).adopted.completed_count, 3);
  assert.equal(decode(retried).revision, 5);
  assert.equal(decode(retried).unchanged, true);
  assert.equal(decode(nextAppend).adopted.completed_count, 4);
  assert.equal(decode(retryAfterMoreText).unchanged, true);
  assert.equal(decode(retryAfterMoreText).adopted.completed_count, 4);
  const [separatePublishedLine] = run([call(1, 'save_scene_checkpoint', { ...checkpoint, track: 'sent',
    expected_revision: 6, body: '===== 资讯助理 =====\n\n下一次公告是我们要继续看的。\n\n配图：https://example.com/news.jpg' })]);
  assert.equal(decode(separatePublishedLine).sent.last_line, '配图：https://example.com/news.jpg');
  assert.equal(decode(separatePublishedLine).sent.last_spoken_line, '下一次公告是我们要继续看的。');
  assert.equal(decode(separatePublishedLine).adopted.completed_count, 4);
  const historicalLine = '今天的交流就到这里，祝大家晚安，我们明天见！';
  const oldLabels = ['===== Jonathan先生 =====\r\n\r\n我们明天再看。', '===== Jonathan先生 =====我们明天再看。',
    '12男：我知道了。', '===== 资讯助理 =====' + historicalLine].join('\r\n');
  const historicalScene = { date: '2026-10-14', session: 'evening' };
  const [countHistorical, savedHistorical, oneLineNewTeacher] = run([
    call(1, 'validate_scene_count', { session: 'evening', body: oldLabels, scope: 'excerpt' }),
    call(2, 'save_scene_checkpoint', { ...checkpoint, ...historicalScene, body: oldLabels, status: 'closed' }),
    call(3, 'validate_speech_format', { mode: 'teacher',
      body: '===== David先生 =====先看价量。\n\n再用收盘验证。' }),
  ]);
  assert.equal(decode(countHistorical).count, 4);
  assert.equal(decode(savedHistorical).adopted.last_speaker, '===== 资讯助理 =====');
  assert.equal(decode(savedHistorical).adopted.last_line, '===== 资讯助理 =====' + historicalLine);
  assert.equal(decode(savedHistorical).adopted.last_spoken_line, historicalLine);
  assert.equal(decode(oneLineNewTeacher).teacher_count, 1);
  assert.equal(decode(oneLineNewTeacher).passed, false);
  assert.ok(decode(oneLineNewTeacher).issues.some((issue) => issue.message.includes('空一行')));
  const [historicalReopen] = run([call(1, 'get_scene_checkpoint', { ...historicalScene, include_body: true })]);
  assert.equal(decode(historicalReopen).adopted.body, oldLabels);
  assert.equal(decode(historicalReopen).adopted.completed_count, 4);

  const resumeDate = '2032-04-22';
  const resumeScene = { date: resumeDate, session: 'evening' };
  const previousMorning = { date: '2032-04-21', session: 'morning' };
  const [savedWaitingForResume, savedSentForResume, refreshedOlderScene] = run([
    call(1, 'save_scene_checkpoint', { ...checkpoint, ...resumeScene }),
    call(2, 'save_scene_checkpoint', { ...checkpoint, ...resumeScene, expected_revision: 1,
      track: 'sent', body: opening, coverage: 'partial', status: 'in_progress', node: '已经开群' }),
    call(3, 'save_scene_checkpoint', { ...checkpoint, ...previousMorning, status: 'closed' }),
  ]);
  assert.equal(decode(savedWaitingForResume).revision, 1);
  assert.equal(decode(savedSentForResume).revision, 2);
  assert.equal(decode(refreshedOlderScene).revision, 1);
  const beforeResumeReads = readdirSync(stateDirectory).sort();
  const [restartedResume, earlierResume, unmatchedResume, invalidResume] = run([
    call(1, 'get_resume_context', {}),
    call(2, 'get_resume_context', { session: 'morning' }),
    call(3, 'get_resume_context', { date: resumeDate, session: 'morning' }),
    call(4, 'get_resume_context', { date: '2032-02-30' }),
  ]);
  const resume = decode(restartedResume);
  assert.equal(resume.status, 'found');
  assert.equal(resume.checkpoint.date, resumeDate);
  assert.equal(resume.checkpoint.session, 'evening');
  assert.equal(resume.checkpoint.revision, 2);
  assert.deepEqual(resume.checkpoint.adopted, {
    status: 'waiting', node: checkpoint.node, stop_after: checkpoint.stop_after, next_step: checkpoint.next_step,
    unresolved: checkpoint.unresolved, last_speaker: '14男：', last_spoken_line: waitingLine,
    last_spoken_line_truncated: false, completed_count: 2,
  });
  assert.equal(resume.checkpoint.sent.last_spoken_line, '下午好。');
  assert.equal(resume.checkpoint.sent.completed_count, null);
  assert.equal(resume.checkpoint.sent.body, undefined);
  assert.equal(resume.checkpoint.sent.tail, undefined);
  assert.equal(decode(earlierResume).checkpoint.date, previousMorning.date);
  assert.equal(decode(unmatchedResume).status, 'empty');
  assert.equal(invalidResume.result.isError, true);
  assert.deepEqual(readdirSync(stateDirectory).sort(), beforeResumeReads);

  const longResumeLine = '学'.repeat(999) + '📈结尾';
  const sameDayMorning = { date: resumeDate, session: 'morning' };
  const [morningForResume, ambiguousResume, explicitResume, morningFull] = run([
    call(1, 'save_scene_checkpoint', { ...checkpoint, ...sameDayMorning, body: `14男：\n${longResumeLine}`, status: 'closed' }),
    call(2, 'get_resume_context', {}),
    call(3, 'get_resume_context', sameDayMorning),
    call(4, 'get_scene_checkpoint', sameDayMorning),
  ]);
  assert.equal(decode(morningForResume).revision, 1);
  assert.deepEqual(decode(ambiguousResume), {
    status: 'choose', timezone: 'Europe/Bucharest', candidates: [
      { ...sameDayMorning, revision: 1, adopted_status: 'closed', sent_status: null },
      { ...resumeScene, revision: 2, adopted_status: 'waiting', sent_status: 'in_progress' },
    ],
  });
  assert.equal(decode(explicitResume).checkpoint.adopted.last_spoken_line, '学'.repeat(999) + '📈');
  assert.equal(decode(explicitResume).checkpoint.adopted.last_spoken_line_truncated, true);
  assert.equal(decode(morningFull).adopted.last_spoken_line, longResumeLine);

  const published = { key: `${date}-bvb-news`, category: 'published', summary: '已发测试资讯',
    status: 'completed', source: '模拟确认' };
  const quiz = { key: `${date}-quiz`, category: 'quiz', summary: '等待 Gabriela 核对答案',
    status: 'open', source: '模拟确认' };
  const morning = { date, session: 'morning', expected_revision: 0, items: [published, quiz] };
  const exercise = { key: `${date}-exercise`, category: 'exercise', summary: '测试课后练习待反馈',
    status: 'open', source: '模拟确认' };
  const afternoon = { date, session: 'afternoon', expected_revision: 0,
    items: [{ ...quiz, status: 'completed', summary: '测试竞猜已核对' }, exercise] };
  const [morningWrite, afternoonWrite, staleHandoff, prematurePublication, morningView] = run([
    call(1, 'save_handoff', morning), call(2, 'save_handoff', afternoon),
    call(3, 'save_handoff', morning),
    call(4, 'save_handoff', { ...morning, expected_revision: 1, items: [{ ...published, status: 'open' }] }),
    call(5, 'get_handoff', { date, through_session: 'morning' }),
  ]);
  assert.equal(decode(morningWrite).revision, 1);
  assert.equal(decode(afternoonWrite).revision, 1);
  assert.equal(staleHandoff.result.isError, true);
  assert.equal(prematurePublication.result.isError, true);
  assert.equal(decode(morningView).today.length, 2);
  assert.equal(decode(morningView).today.find((item) => item.key === quiz.key).status, 'open');

  const [eveningView, tomorrow, noLookback] = run([
    call(1, 'get_handoff', { date }),
    call(2, 'get_handoff', { date: '2026-11-20', through_session: 'morning' }),
    call(3, 'get_handoff', { date: '2026-11-20', lookback_days: 0 }),
  ]);
  assert.equal(decode(eveningView).today.find((item) => item.key === quiz.key).status, 'completed');
  assert.equal(decode(eveningView).today.filter((item) => item.key === quiz.key).length, 1);
  assert.deepEqual(decode(tomorrow).carryover.map((item) => item.key), [exercise.key]);
  assert.deepEqual(decode(noLookback).carryover, []);

  const source = { publisher: '模拟来源', url: 'https://market.example/update',
    retrieved_at: `${date}T15:15:00+02:00`, published_at: `${date}T15:11:00+02:00` };
  const quote = { date, key: 'market-reading', market: 'us', session: 'premarket', expected_revision: 0,
    status: 'verified', claim: '模拟盘前报价', sources: [source], data_asof: `${date}T15:10:00+02:00` };
  const [quoteWrite, futuresWrite, noSource, noOffset, invalidUrl] = run([
    call(1, 'save_fact_check', quote),
    call(2, 'save_fact_check', { ...quote, session: 'futures', claim: '模拟期货报价' }),
    call(3, 'save_fact_check', { ...quote, key: 'no-source', sources: [] }),
    call(4, 'save_fact_check', { ...quote, key: 'no-offset', data_asof: `${date}T15:10:00` }),
    call(5, 'save_fact_check', { ...quote, key: 'invalid-url', sources: [{ ...source, url: 'file:///local' }] }),
  ]);
  assert.equal(decode(quoteWrite).revision, 1);
  assert.equal(decode(futuresWrite).revision, 1);
  assert.equal(noSource.result.isError, true);
  assert.equal(noOffset.result.isError, true);
  assert.equal(invalidUrl.result.isError, true);
  const corrected = { ...quote, expected_revision: 1, status: 'conflicting', note: '模拟来源之间存在差异' };
  const [beforeCorrection, conflict, staleFact, afterCorrection, futures, wrongDay, wrongMarket] = run([
    call(1, 'get_fact_checks', { date, market: 'us', session: 'premarket' }),
    call(2, 'save_fact_check', corrected),
    call(3, 'save_fact_check', corrected),
    call(4, 'get_fact_checks', { date, market: 'us', session: 'premarket' }),
    call(5, 'get_fact_checks', { date, market: 'us', session: 'futures' }),
    call(6, 'get_fact_checks', { date: '2026-11-20' }),
    call(7, 'get_fact_checks', { date, market: 'bvb' }),
  ]);
  assert.equal(decode(beforeCorrection).records[0].status, 'verified');
  assert.equal(decode(beforeCorrection).records[0].sources[0].published_at, source.published_at);
  assert.equal(decode(beforeCorrection).records[0].data_asof, quote.data_asof);
  assert.equal(decode(conflict).revision, 2);
  assert.equal(staleFact.result.isError, true);
  assert.equal(decode(afterCorrection).records.length, 1);
  assert.equal(decode(afterCorrection).records[0].status, 'conflicting');
  assert.equal(decode(futures).records[0].claim, '模拟期货报价');
  assert.equal(decode(wrongDay).total, 0);
  assert.equal(decode(wrongMarket).total, 0);
  assert.equal(decode(run([call(1, 'get_agenda', {})])[0]).next_task, null);
  const fullBody = '教学模拟。\n\n===== David先生 =====\n先看收盘。😊';
  const entry = (id, status, body) => [
    '<<< WESTCOTT HANDOFF >>>', `编号：${id}`, `日期：${date}`, '场次：晚间', `状态：${status}`,
    '标题：回踩课片段', '停点：先看收盘。😊', '下一步：继续解释观察线索的用途。',
    '<<< WESTCOTT BODY >>>', body, '<<< WESTCOTT END >>>',
  ].join('\n');
  const confirmed = entry('test-confirmed', '确认稿', fullBody);
  const working = entry('test-working', '草稿', '未确认内容');
  const handoffDoc = ['Westcott 手机交接箱', 'WESTCOTT-HANDOFF v1', confirmed, working].join('\n\n');
  const preview = (id, doc = handoffDoc) => ({ document_text: doc, ...(id ? { entry_id: id } : {}) });
  const [index, selected, draftPreview, duplicate, malformed] = run([
    call(1, 'preview_mobile_handoff', preview()),
    call(2, 'preview_mobile_handoff', preview('test-confirmed', handoffDoc.replaceAll('\n', '\r\n'))),
    call(3, 'preview_mobile_handoff', preview('test-working')),
    call(4, 'preview_mobile_handoff', preview(null, `${handoffDoc}\n${confirmed}`)),
    call(5, 'preview_mobile_handoff', preview(null, confirmed)),
  ]);
  assert.equal(decode(index).entries.length, 2);
  assert.equal(Object.hasOwn(decode(index).entries[0], 'body'), false);
  const selectedEntry = decode(selected).entry;
  assert.equal(selectedEntry.body, fullBody);
  assert.equal(duplicate.result.isError, true);
  assert.equal(malformed.result.isError, true);
  const importArgs = { ...preview(selectedEntry.id), fingerprint: selectedEntry.fingerprint };
  const [mobile, oldFingerprint, notConfirmed, missingPhase] = run([
    call(1, 'import_mobile_handoff', importArgs),
    call(2, 'import_mobile_handoff', { ...importArgs, document_text: handoffDoc.replace('先看收盘。😊', '先看量能。') }),
    call(3, 'import_mobile_handoff', { ...preview('test-working'), fingerprint: decode(draftPreview).entry.fingerprint }),
    call(4, 'import_mobile_handoff', { ...importArgs, phase_id: 'does-not-exist', task_id: 'fake-task' }),
  ]);
  assert.equal(decode(mobile).reused, false);
  assert.equal(oldFingerprint.result.isError, true);
  assert.equal(notConfirmed.result.isError, true);
  assert.equal(missingPhase.result.isError, true);
  const mobileId = decode(mobile).id;
  const [retry, localDraft, unchangedAgenda] = run([
    call(1, 'import_mobile_handoff', importArgs),
    call(2, 'read_record', { id: mobileId }),
    call(3, 'get_agenda', {}),
  ]);
  assert.deepEqual(decode(retry), { id: mobileId, kind: 'draft', reused: true });
  assert.equal(decode(localDraft).body, fullBody);
  assert.equal(decode(localDraft).mobile_handoff.stop, selectedEntry.stop);
  assert.deepEqual(decode(unchangedAgenda), decode(done));
  const [adoptMobile] = run([call(1, 'record_adoption', { id: mobileId, approval: '模拟认可手机中段。' })]);
  const mobilePoint = { date, session: 'evening', expected_revision: 0, track: 'adopted', status: 'in_progress',
    adopted_record_id: decode(adoptMobile).id, node: '课堂中段', unresolved: [],
    source: { ref: decode(selected).source, revision: selectedEntry.fingerprint }, confirmation: '模拟保存手机稿停点。' };
  const [wrongMobileScene, mobileScene, mobileStillPending] = run([
    call(1, 'save_scene_checkpoint', { ...mobilePoint, session: 'morning' }),
    call(2, 'save_scene_checkpoint', mobilePoint),
    call(3, 'complete_lesson', { id: decode(adoptMobile).id, confirmation: '模拟误把手机中段当整课。' }),
  ]);
  assert.equal(wrongMobileScene.result.isError, true);
  assert.equal(decode(mobileScene).adopted.last_line, selectedEntry.stop);
  assert.equal(decode(mobileScene).adopted.completed_count, null);
  assert.equal(decode(mobileScene).adopted.confirmed_count, 1);
  assert.equal(mobileStillPending.result.isError, true);
  const castSource = '模拟验收条目';
  const profile12 = { member_id: '12', expected_revision: 0, label: '12男', availability: 'active',
    voice: '通常用短句追问一个实际判断条件', sample_lines: ['那先看收盘？'], source: castSource };
  const profile51 = { ...profile12, member_id: '51', label: '51男', availability: 'archived' };
  const scriptQuestion = { member_id: '12', scope: 'script', key: 'close-condition', expected_revision: 0,
    date, session: 'evening', activity_type: 'question', status: 'open', summary: '下次先看收盘能否守住吗？', source: castSource };
  const [missingIdentity, saved12, saved51, labelMismatch, staleProfile, defaultRoster, fullRoster, noRelease] = run([
    call(1, 'save_member_activity', { ...scriptQuestion, member_id: '99' }),
    call(2, 'save_member_profile', profile12),
    call(3, 'save_member_profile', profile51),
    call(4, 'save_member_profile', { ...profile12, member_id: '37' }),
    call(5, 'save_member_profile', profile12),
    call(6, 'get_cast_context', { as_of: date }),
    call(7, 'get_cast_context', { as_of: date, include_inactive: true }),
    call(8, 'save_member_profile', { ...profile51, member_id: '53', label: '53男', availability: 'active', expected_revision: 0 }),
  ]);
  assert.equal(missingIdentity.result.isError, true);
  assert.equal(decode(saved12).revision, 1);
  assert.equal(decode(saved51).availability, 'archived');
  assert.equal(labelMismatch.result.isError, true);
  assert.equal(staleProfile.result.isError, true);
  assert.equal(decode(defaultRoster).members.length, 51);
  assert.ok(decode(defaultRoster).members.some(({ profile }) => profile.member_id === '52'));
  assert.equal(decode(defaultRoster).staff.find((member) => member.label === 'David先生').name, 'David Miller');
  assert.equal(decode(fullRoster).members.length, 65);
  assert.equal(decode(fullRoster).members.find(({ profile }) => profile.member_id === '51').profile.availability, 'archived');
  assert.equal(noRelease.result.isError, true);
  const [shortlist, focusedCast] = run([
    call(1, 'get_cast_context', { as_of: date, detail: 'selection' }),
    call(2, 'get_cast_context', { as_of: date, detail: 'focused', member_ids: ['12'] }),
  ]);
  assert.equal(decode(shortlist).members.length, decode(defaultRoster).members.length);
  assert.equal(decode(shortlist).members[11].profile.voice, undefined);
  assert.ok(JSON.stringify(decode(shortlist)).length < JSON.stringify(decode(defaultRoster)).length);
  assert.deepEqual(decode(focusedCast).members.map(({ profile }) => profile.member_id), ['12']);
  assert.equal(decode(focusedCast).members[0].profile.voice, profile12.voice);
  const liveQuestion = { ...scriptQuestion, scope: 'live', summary: '真实反馈路径的测试内容' };
  const appearance = { ...scriptQuestion, key: 'shape-scene', activity_type: 'appearance',
    status: undefined, summary: '形态课发言' };
  const progress = { ...appearance, key: 'shape-progress', activity_type: 'progress', summary: '已经能先观察收盘' };
  const [saveQuestion, saveLive, saveAppearance, saveProgress, invalidStatus, preEvening] = run([
    call(1, 'save_member_activity', scriptQuestion), call(2, 'save_member_activity', liveQuestion),
    call(3, 'save_member_activity', appearance), call(4, 'save_member_activity', progress),
    call(5, 'save_member_activity', { ...appearance, status: 'open' }),
    call(6, 'get_cast_context', { as_of: date, through_session: 'afternoon', member_ids: ['12'] }),
  ]);
  for (const response of [saveQuestion, saveLive, saveAppearance, saveProgress]) assert.equal(decode(response).revision, 1);
  assert.equal(invalidStatus.result.isError, true);
  assert.equal(decode(preEvening).members[0].last_appearance, null);
  const followingDay = '2026-11-20';
  const resolution = { ...scriptQuestion, date: followingDay, session: 'morning', expected_revision: 1,
    status: 'resolved', summary: 'David 解释何时重新评估突破位' };
  const [resolveQuestion, wrongDate, oldView, nextScene, liveOnly, specificProfile] = run([
    call(1, 'save_member_activity', resolution),
    call(2, 'save_member_activity', { ...scriptQuestion, expected_revision: 2 }),
    call(3, 'get_cast_context', { as_of: date, member_ids: ['12'] }),
    call(4, 'get_cast_context', { as_of: followingDay, through_session: 'morning', member_ids: ['12'] }),
    call(5, 'get_cast_context', { as_of: followingDay, scope: 'live', member_ids: ['12'] }),
    call(6, 'get_cast_context', { as_of: date, member_ids: ['51', '99'] }),
  ]);
  assert.equal(decode(resolveQuestion).revision, 2);
  assert.equal(wrongDate.result.isError, true);
  const scriptOld = decode(oldView).members[0];
  assert.equal(scriptOld.questions[0].status, 'open');
  assert.equal(scriptOld.questions[0].latest_revision, 2);
  assert.equal(scriptOld.last_appearance.summary, appearance.summary);
  assert.equal(scriptOld.latest_progress.summary, progress.summary);
  assert.equal(decode(nextScene).members[0].questions[0].status, 'resolved');
  assert.equal(decode(liveOnly).members[0].questions[0].summary, liveQuestion.summary);
  assert.equal(decode(liveOnly).members[0].last_appearance, null);
  assert.equal(decode(specificProfile).members[0].profile.availability, 'archived');
  assert.deepEqual(decode(specificProfile).missing_member_ids, ['99']);
  const candles = [
    { open: 97, high: 101, low: 96, close: 100, volume: 150 },
    { open: 100, high: 105, low: 99, close: 104, volume: 260 },
    { open: 103, high: 104, low: 100, close: 101, volume: 125 },
    { open: 101, high: 102, low: 96, close: 97, volume: 300 },
    { open: 97.5, high: 103, low: 97, close: 101.5, volume: 320 },
  ];
  const kit = { adopted_id: adopted.id, learning_goal: '学会给回踩判断写反证。',
    lecturer_points: ['标记关键位。', '观察第四根 < 100 之后怎么更新判断。'],
    exercise: { prompt: '假设第五根重新站回，接下来你还要看到什么？', answer: '讲师专用测试答案' },
    chart: { title: '回踩课堂示意图', level: 100, candles, hypothetical_from: 5 } };
  const [badDraft, badCandle, badScenario, lesson, repeatLesson] = run([
    call(1, 'build_lesson_kit', { ...kit, adopted_id: id }),
    call(2, 'build_lesson_kit', { ...kit, chart: { ...kit.chart, candles: [{ ...candles[0], low: 99 }, ...candles.slice(1)] } }),
    call(3, 'build_lesson_kit', { ...kit, chart: { ...kit.chart, hypothetical_from: 6 } }),
    call(4, 'build_lesson_kit', kit), call(5, 'build_lesson_kit', kit),
  ]);
  assert.equal(badDraft.result.isError, true);
  assert.equal(badCandle.result.isError, true);
  assert.equal(badScenario.result.isError, true);
  const output = decode(lesson);
  assert.equal(output.reused, false);
  assert.deepEqual(decode(repeatLesson), { ...output, reused: true });
  assert.equal(output.files.length, 4);
  const assets = Object.fromEntries(output.files.map(({ name, path }) => [name, readFileSync(path, 'utf8')]));
  assert.match(assets['lesson-chart.svg'], /data-candle="4" data-scenario="example"/);
  assert.match(assets['lesson-chart.svg'], /data-candle="5" data-scenario="hypothetical"/);
  assert.match(assets['lesson-chart.svg'], /绿涨、红跌、灰平盘/);
  assert.match(assets['lesson.html'], /第四根 &lt; 100/);
  assert.match(assets['lesson.html'], /lesson-chart-3840x2160\.png/);
  assert.equal(assets['lesson.html'].includes(kit.exercise.answer), false);
  assert.equal(assets['exercise.md'].includes(kit.exercise.answer), false);
  assert.equal(assets['teacher-notes.md'].includes(kit.exercise.answer), true);
  const formatReference = readFileSync(new URL('../reference/speech-format.md', import.meta.url), 'utf8');
  const [newsExample, teacher] = [...formatReference.matchAll(/^~~~text\n([\s\S]*?)\n~~~$/gm)].map((match) => match[1]);
  const newsImage = 'https://cdn.example.com/events/market.jpg';
  const newsPage = 'https://example.com/market-event';
  const news = newsExample.replace('<已核实的事件配图直链>', newsImage);
  assert.ok(news && teacher);
  const opener = '===== 资讯助理 =====\n\n🌤 大家早上好！';
  const closer = '===== 资讯助理 =====\n\n上午就先聊到这里，我们下午见。';
  const mixed = [opener, news, '12男：\n那图上还要看什么？', teacher, closer].join('\n\n');
  const [newsResult, teacherResult, mixedResult, splitNews, splitTeacher, wrongCountry, oldNews, noGap,
    missingPicture, wrongPicture] = run([
    call(1, 'validate_speech_format', { body: news, mode: 'news', expected_image_urls: [newsImage] }),
    call(2, 'validate_speech_format', { body: teacher, mode: 'teacher' }),
    call(3, 'validate_speech_format', { body: mixed }),
    call(4, 'validate_speech_format', { body: news.replace('\n\n对罗马尼亚', '\n\n===== 资讯助理 =====\n\n对罗马尼亚') }),
    call(5, 'validate_speech_format', { body: teacher.replace('\n\n所以，', '\n\n===== David先生 =====\n\n所以，') }),
    call(6, 'validate_speech_format', { body: news.replace('🇺🇸', '🇷🇴'), mode: 'news' }),
    call(7, 'validate_speech_format', { body: news.replace('财经新闻｜', '财经资讯快报｜') }),
    call(8, 'validate_speech_format', { body: teacher.replace('===== David先生 =====', '===== 先生 =====').replace('\n\n', '\n') }),
    call(9, 'validate_speech_format', { body: news.split('\n\n配图：')[0], mode: 'news' }),
    call(10, 'validate_speech_format', { body: news, mode: 'news', expected_image_urls: [newsPage] }),
  ]);
  assert.deepEqual(decode(newsResult), { passed: true, news_count: 1, teacher_count: 0, issues: [] });
  assert.deepEqual(decode(teacherResult), { passed: true, news_count: 0, teacher_count: 1, issues: [] });
  assert.deepEqual(decode(mixedResult), { passed: true, news_count: 1, teacher_count: 1, issues: [] });
  for (const response of [splitNews, wrongCountry, oldNews, noGap, missingPicture, wrongPicture]) {
    assert.equal(decode(response).passed, false);
  }
  assert.equal(decode(splitTeacher).passed, true);
  assert.ok(decode(noGap).issues.some((issue) => issue.message.includes('角色标记与正文')));
  const chineseBad = [
    '===== 资讯助理 =====\n\n家人们，等着上车。',
    '12男：老师太到位了，学到了 😅🔥',
    '===== David先生 =====\n\n主力洗 盘，接着就能抄底。',
  ].join('\n\n');
  const lengthy = '37女：我还没看报告。想先看完今天的收盘数据，再回头对照前一个交易日的成交量。等报告出来我再看一次。🙂🤔';
  const [chineseVoice, shortVoice, banAll, nonChinese, tooLong, hypeVoice, commonTerms, colorBad, colorGood] = run([
    call(1, 'validate_whatsapp_voice', { body: mixed }),
    call(2, 'validate_whatsapp_voice', { body: '12男：要是收盘跌到这条线下呢？' }),
    call(3, 'validate_whatsapp_voice', { body: chineseBad }),
    call(4, 'validate_whatsapp_voice', { body: '12男：I am watching the close.' }),
    call(5, 'validate_whatsapp_voice', { body: lengthy }),
    call(6, 'validate_whatsapp_voice', { body: '12男：这下肯定 to the moon! 🚀' }),
    call(7, 'validate_whatsapp_voice', { body: '37女：我看了 BVB 的 BET 和成交量。' }),
    call(8, 'validate_whatsapp_voice', { body: '12男：BET今天上涨，红色的上涨。' }),
    call(9, 'validate_whatsapp_voice', { body: '12男：BET今天上涨，见绿；TLV下跌，飘红。' }),
  ]);
  assert.deepEqual(decode(chineseVoice), { passed: true, checked_messages: 5, member_messages: 1,
    needs_review: false, issues: [] });
  assert.equal(decode(shortVoice).passed, true);
  assert.equal(decode(banAll).passed, false);
  for (const role of ['===== 资讯助理 =====', '12男：', '===== David先生 =====']) {
    assert.ok(decode(banAll).issues.some((issue) => issue.role === role && issue.severity === 'block'));
  }
  assert.ok(decode(banAll).issues.some((issue) => issue.term === '主力洗盘'));
  assert.ok(decode(nonChinese).issues.some((issue) => issue.type === 'language'));
  assert.ok(decode(tooLong).issues.some((issue) => issue.type === 'emoji_density'));
  assert.ok(decode(tooLong).issues.some((issue) => issue.type === 'member_length'));
  assert.equal(decode(hypeVoice).passed, false);
  assert.equal(decode(commonTerms).passed, true);
  assert.ok(decode(colorBad).issues.some((issue) => issue.type === 'color'));
  assert.equal(decode(colorGood).passed, true);
  const monday = '2026-11-23';
  const marketSource = 'https://www.nyse.com/trade/trading-information';
  const previousSession = { previous_us_session_date: '2026-11-20', previous_us_session_source: marketSource };
  const imageCheck = { method: 'http_image', observed_url: newsImage, checked_at: '2026-11-23T11:02:00+02:00',
    content_type: 'image/jpeg', event_match: '模拟来源页将该图标为这项测试公告配图。' };
  const earlierNews = { kind: 'news', area: 'romania', title: '测试公告 A', published_at: '2026-11-23T10:00:00+02:00',
    image_url: newsImage, image_source_url: newsPage, image_check: imageCheck };
  const laterNews = { ...earlierNews, area: 'us', title: '测试公告 B', published_at: '2026-11-23T09:00:00Z' };
  const priorNight = { ...laterNews, kind: 'us_overnight_major', title: '测试前夜事件', published_at: '2026-11-22T23:10:00+02:00' };
  const priorClose = { kind: 'us_previous_session_indicators', area: 'us', title: '测试美股前一交易日收盘',
    data_asof: '2026-11-20T16:00:00-05:00' };
  const newsBatch = (entries, session = 'morning', extra = {}) => ({ date: monday, session, entries, ...extra });
  const [ordered, backwards, mondayRecap, afternoonRecap, staleOrdinary, noTime, localLate, holidayMismatch,
    foreignDate, noImage, sameAsArticle] = run([
    call(1, 'validate_news_broadcast', newsBatch([earlierNews, laterNews])),
    call(2, 'validate_news_broadcast', newsBatch([laterNews, earlierNews])),
    call(3, 'validate_news_broadcast', newsBatch([priorClose, priorNight, earlierNews],
      'morning', previousSession)),
    call(4, 'validate_news_broadcast', newsBatch([priorNight, priorClose], 'afternoon', previousSession)),
    call(5, 'validate_news_broadcast', newsBatch([{ ...priorNight, kind: 'news' }])),
    call(6, 'validate_news_broadcast', newsBatch([{ ...earlierNews, published_at: undefined }])),
    call(7, 'validate_news_broadcast', newsBatch([{ ...earlierNews, published_at: '2026-11-23T23:30:00Z' }], 'evening')),
    call(8, 'validate_news_broadcast', newsBatch([priorClose], 'morning', { ...previousSession, previous_us_session_date: '2026-11-19' })),
    call(9, 'validate_news_broadcast', newsBatch([{ ...laterNews, published_at: '2026-11-22T23:30:00-05:00' }])),
    call(10, 'validate_news_broadcast', newsBatch([{ ...earlierNews, image_url: undefined }])),
    call(11, 'validate_news_broadcast', newsBatch([{ ...earlierNews, image_url: newsPage }])),
  ]);
  assert.equal(decode(ordered).passed, true);
  assert.equal(decode(mondayRecap).passed, true);
  assert.equal(decode(foreignDate).passed, true);
  for (const result of [backwards, afternoonRecap, staleOrdinary, noTime, localLate, holidayMismatch, noImage, sameAsArticle]) {
    assert.equal(decode(result).passed, false);
  }
  const checkedImages = decode(ordered).expected_news;
  assert.deepEqual(checkedImages, [
    { country: '罗马尼亚', title: earlierNews.title, image_url: newsImage },
    { country: '美国', title: laterNews.title, image_url: newsImage },
  ]);
  const evidenceOptions = { require_source_evidence: true, as_of: '2026-11-23T14:00:00+02:00' };
  const evidenceBatch = (entry) => newsBatch([entry], 'afternoon', evidenceOptions);
  const sourceVerified = { ...earlierNews, source_url: newsPage,
    image_check: { ...imageCheck, relation: 'event' } };
  const evidenceCases = [
    [sourceVerified, true, null],
    [{ ...sourceVerified, source_url: undefined }, false, /原始来源页/],
    [{ ...sourceVerified, image_check: imageCheck }, false, /官方替代图/],
    [{ ...sourceVerified, image_check: { ...imageCheck, relation: 'unrelated' } }, false, /不同报告期/],
    [{ ...sourceVerified, image_check: { ...imageCheck, relation: 'official_subject' } }, true, null],
    [{ ...sourceVerified, published_at: '2026-11-22T18:00:00+02:00' }, false, /与播报日期不符/],
  ];
  const evidenceResults = run(evidenceCases.map(([entry], index) =>
    call(index + 1, 'validate_news_broadcast', evidenceBatch(entry))));
  for (const [index, [, passed, message]] of evidenceCases.entries()) {
    const checked = decode(evidenceResults[index]);
    assert.equal(checked.passed, passed, JSON.stringify(checked.issues));
    assert.equal(checked.expected_news.length, passed ? 1 : 0);
    if (message) assert.match(checked.issues.map((item) => item.message).join(' '), message);
  }
  const [emptyEvidence, noCutoff, emptyPreview] = run([
    call(1, 'validate_news_broadcast', newsBatch([], 'afternoon', evidenceOptions)),
    call(2, 'validate_news_broadcast', { ...evidenceBatch(sourceVerified), as_of: undefined }),
    call(3, 'validate_news_broadcast', newsBatch([], 'afternoon')),
  ]).map(decode);
  assert.equal(emptyEvidence.passed, false);
  assert.match(emptyEvidence.issues[0].message, /一条候选内容/);
  assert.equal(noCutoff.passed, false);
  assert.match(noCutoff.issues[0].message, /播报截点/);
  assert.deepEqual(noCutoff.expected_news, []);
  assert.equal(emptyPreview.passed, true);
  const extensionless = 'https://cdn.example.com/content?image=event-a';
  const imageWithoutExtension = { ...earlierNews, image_url: extensionless,
    image_check: { ...imageCheck, observed_url: extensionless, method: 'rendered', content_type: undefined } };
  const [priorMorning, overnightEdge, dayStart, midAfter, noCheck, html, mismatchedObservation, extensionlessResult, missingCalendar] = run([
    call(1, 'validate_news_broadcast', newsBatch([{ ...priorNight, published_at: '2026-11-22T10:00:00+02:00' }])),
    call(2, 'validate_news_broadcast', newsBatch([{ ...priorNight, published_at: '2026-11-22T18:00:00+02:00' }])),
    call(3, 'validate_news_broadcast', newsBatch([{ ...priorNight, published_at: '2026-11-23T00:01:00+02:00' }])),
    call(4, 'validate_news_broadcast', newsBatch([{ ...priorNight, published_at: '2026-11-23T00:01:00+02:00', kind: 'news' }])),
    call(5, 'validate_news_broadcast', newsBatch([{ ...earlierNews, image_url: extensionless, image_check: undefined }])),
    call(6, 'validate_news_broadcast', newsBatch([{ ...earlierNews, image_check: { ...imageCheck, content_type: 'text/html' } }])),
    call(7, 'validate_news_broadcast', newsBatch([{ ...earlierNews, image_check: { ...imageCheck, observed_url: extensionless } }])),
    call(8, 'validate_news_broadcast', newsBatch([imageWithoutExtension])),
    call(9, 'validate_news_broadcast', newsBatch([priorClose], 'morning', { previous_us_session_date: '2026-11-20' })),
  ]);
  for (const result of [priorMorning, dayStart, noCheck, html, mismatchedObservation, missingCalendar]) {
    assert.equal(decode(result).passed, false);
    assert.deepEqual(decode(result).expected_news, []);
  }
  for (const result of [overnightEdge, midAfter, extensionlessResult]) assert.equal(decode(result).passed, true);
  const skippedFriday = { ...priorClose, data_asof: '2026-11-19T16:00:00-05:00' };
  const thanksgiving = '2026-11-26';
  const holidayRecap = { date: '2026-11-27', session: 'morning',
    entries: [{ ...priorClose, data_asof: '2026-11-25T16:00:00-05:00' }],
    previous_us_session_date: '2026-11-25', previous_us_session_source: marketSource };
  const [skippedTradingDay, missingHoliday, confirmedHoliday, earlyCloseReference, badHoliday] = run([
    call(1, 'validate_news_broadcast', newsBatch([skippedFriday], 'morning',
      { ...previousSession, previous_us_session_date: '2026-11-19' })),
    call(2, 'validate_news_broadcast', holidayRecap),
    call(3, 'validate_news_broadcast', { ...holidayRecap, closed_us_session_dates: [thanksgiving] }),
    call(4, 'validate_news_broadcast', { ...holidayRecap, date: '2026-11-30', previous_us_session_date: '2026-11-27',
      entries: [{ ...priorClose, data_asof: '2026-11-27T13:00:00-05:00' }] }),
    call(5, 'validate_news_broadcast', { ...holidayRecap, closed_us_session_dates: ['2026-02-30'] }),
  ]);
  assert.equal(decode(skippedTradingDay).passed, false);
  assert.equal(decode(missingHoliday).passed, false);
  assert.equal(decode(confirmedHoliday).passed, true);
  assert.equal(decode(earlyCloseReference).passed, true);
  assert.equal(badHoliday.result.isError, true);
  const bulletinA = news.replace(/🇺🇸 美国财经新闻｜[^\n]+/, '🇷🇴 罗马尼亚财经新闻｜' + earlierNews.title);
  const bulletinB = news.replace(/🇺🇸 美国财经新闻｜[^\n]+/, '🇺🇸 美国财经新闻｜' + laterNews.title)
    .replace(newsImage, extensionless);
  const approvedPair = [checkedImages[0], { ...checkedImages[1], image_url: extensionless }];
  const [matchedPair, swappedImages, swappedHeadlines] = run([
    call(1, 'validate_speech_format', { body: bulletinA + '\n\n' + bulletinB, expected_news: approvedPair }),
    call(2, 'validate_speech_format', { body: bulletinA.replace(newsImage, extensionless) + '\n\n'
      + bulletinB.replace(extensionless, newsImage), expected_news: approvedPair }),
    call(3, 'validate_speech_format', { body: bulletinB + '\n\n' + bulletinA, expected_news: approvedPair }),
  ]);
  assert.equal(decode(matchedPair).passed, true);
  assert.equal(decode(swappedImages).passed, false);
  assert.equal(decode(swappedHeadlines).passed, false);
  const [recordSent, afternoonOld, afternoonNew, optionalCutoff] = run([
    call(1, 'save_handoff', { date: monday, session: 'morning', expected_revision: 0, items: [{
      key: monday + '-test-news', category: 'published', status: 'completed',
      summary: '测试新闻已发', source: '测试原文', news_published_at: laterNews.published_at,
    }] }),
    call(2, 'validate_news_broadcast', newsBatch([earlierNews], 'afternoon')),
    call(3, 'validate_news_broadcast', newsBatch([{ ...laterNews, published_at: '2026-11-23T12:00:00+02:00' }], 'afternoon')),
    call(4, 'validate_news_broadcast', newsBatch([laterNews], 'afternoon', { as_of: '2026-11-23T10:30:00+02:00' })),
  ]);
  assert.equal(decode(recordSent).items[0].news_published_at, laterNews.published_at);
  assert.equal(decode(afternoonOld).passed, false);
  assert.equal(decode(afternoonOld).previous_broadcast_published_at, laterNews.published_at);
  assert.equal(decode(afternoonNew).passed, true);
  assert.equal(decode(optionalCutoff).passed, false);

  const openSpec = { date: monday, market: 'us', calendar_status: 'open', calendar_source: marketSource,
    as_of: '2026-11-23T16:25:00+02:00' };
  const [winterOpen, changedClock, closedMarket, calendarMissing, waitingForDelay, delayed, certified, staleSnapshot,
    bvbOpening, badWeekend] = run([
    call(1, 'plan_market_open', openSpec),
    call(2, 'plan_market_open', { ...openSpec, date: '2026-10-27', as_of: '2026-10-27T15:00:00+02:00' }),
    call(3, 'plan_market_open', { ...openSpec, date: '2026-11-26', as_of: '2026-11-26T17:00:00+02:00',
      calendar_status: 'closed' }),
    call(4, 'plan_market_open', { ...openSpec, calendar_source: undefined }),
    call(5, 'plan_market_open', { ...openSpec, as_of: '2026-11-23T16:36:00+02:00' }),
    call(6, 'plan_market_open', { ...openSpec, as_of: '2026-11-23T17:00:00+02:00' }),
    call(7, 'plan_market_open', { ...openSpec, as_of: '2026-11-23T17:00:00+02:00',
      snapshot_as_of: '2026-11-23T09:45:00-05:00', snapshot_confirmed: true }),
    call(8, 'plan_market_open', { ...openSpec, as_of: '2026-11-23T17:00:00+02:00',
      snapshot_as_of: '2026-11-23T09:25:00-05:00', snapshot_confirmed: true }),
    call(9, 'plan_market_open', { ...openSpec, market: 'bvb', as_of: '2026-11-23T10:05:00+02:00',
      calendar_source: 'https://www.bvb.ro/TradingAndStatistics/TradingSessionSchedule' }),
    call(10, 'plan_market_open', { ...openSpec, date: '2026-11-22', as_of: '2026-11-22T15:00:00+02:00' }),
  ]);
  assert.equal(decode(winterOpen).opens_bucharest, monday + 'T16:30');
  assert.equal(decode(winterOpen).status, 'waiting_for_open');
  assert.equal(decode(changedClock).opens_bucharest, '2026-10-27T15:30');
  assert.equal(decode(closedMarket).status, 'closed');
  assert.equal(decode(closedMarket).opens_bucharest, undefined);
  assert.equal(decode(calendarMissing).status, 'calendar_unverified');
  assert.equal(decode(waitingForDelay).status, 'waiting_for_delay');
  assert.equal(decode(delayed).status, 'waiting_for_snapshot');
  assert.equal(decode(certified).status, 'ready');
  assert.equal(staleSnapshot.result.isError, true);
  assert.equal(decode(bvbOpening).opens_bucharest, monday + 'T10:00');
  assert.equal(decode(bvbOpening).suggested_check_bucharest, monday + 'T10:20');
  assert.equal(badWeekend.result.isError, true);

  const sceneOf = (count) => [news, teacher, ...Array.from({ length: count - 2 }, () => '12男：\n我先等收盘。')].join('\n\n');
  const wed = '2026-11-25';
  const thu = '2026-11-26';
  const wednesdayOpening = '===== 资讯助理 =====\n\n🌤 大家下午好！欢迎回到 ELLERY WESTCOTT 社群！\n\n上午的竞猜，选好后可以发给顾问 Gabriela。';
  const countInputs = [
    [{ session: 'morning', body: sceneOf(35) }, true, 35],
    [{ session: 'evening', body: sceneOf(40) }, false, 40],
    [{ session: 'morning', body: sceneOf(28) }, false, 28],
    [{ session: 'afternoon', body: sceneOf(42) }, false, 42],
    [{ session: 'morning', body: sceneOf(32) }, true, 32],
    [{ session: 'afternoon', body: sceneOf(34) }, true, 34],
    [{ session: 'afternoon', date: thu, body: sceneOf(39) }, true, 39],
    [{ session: 'afternoon', date: wed, body: sceneOf(39), allow_teacher: true }, true, 39],
    [{ session: 'afternoon', date: wed, body: sceneOf(39), allow_teacher: true, recommendation_followup: true }, true, 39],
    [{ session: 'afternoon', date: thu, body: sceneOf(40), recommendation_followup: true }, true, 40],
    [{ session: 'evening', scope: 'excerpt', body: teacher }, true, 1],
    [{ session: 'morning', body: sceneOf(35) + '\n\n12男：' }, false, 35],
    [{ session: 'afternoon', date: wed, starts_scene: true, scope: 'excerpt',
      body: wednesdayOpening + '\n\n46男 Andrei：先看看今天的新闻。' }, true, 2],
    [{ session: 'afternoon', date: wed, starts_scene: true, scope: 'excerpt',
      body: '===== 资讯助理 =====\n\n大家好，下午场开始。\n\n' + teacher }, false, 2],
    [{ session: 'afternoon', date: wed, scope: 'excerpt', body: wednesdayOpening + '\n\n' + teacher }, false, 2],
  ];
  const sceneChecks = run(countInputs.map(([input], i) => call(i + 1, 'validate_scene_count', input)));
  for (const [index, [, passed, count]] of countInputs.entries()) {
    const result = decode(sceneChecks[index]);
    assert.equal(result.passed, passed, JSON.stringify(result.issues));
    assert.equal(result.count, count);
  }
  assert.match(decode(sceneChecks[13]).issues.map(({ message }) => message).join(' '), /先生.*开群/u);
  const basic = { member_id: '12', scope: 'script', expected_revision: 0, session: 'evening',
    activity_type: 'appearance', speaking_move: 'basic_question', summary: '刚开始问图上的线怎么看。', source: '测试采用稿' };
  const firstStage = { member_id: '12', scope: 'script', expected_revision: 0, session: 'evening',
    activity_type: 'progress', learner_stage: 'newcomer', summary: '刚加入，通过基础问话认图。', source: '测试采用稿' };
  const [grouped12, grouped14, grouped37, updatedVoice, firstAppearance, firstProgress, secondAppearance,
    laterBasic, watched, skeptical, liveGrowth] = run([
    call(1, 'save_member_profile', { ...profile12, expected_revision: 1, discussion_group: '观望组' }),
    call(2, 'save_member_profile', { ...profile12, member_id: '14', label: '14男', discussion_group: '质疑组' }),
    call(3, 'save_member_profile', { ...profile12, member_id: '37', label: '37女', discussion_group: '信随组' }),
    call(4, 'save_member_profile', { ...profile12, expected_revision: 2, voice: '倾向先观察一项条件' }),
    call(5, 'save_member_activity', { ...basic, date: '2026-11-21', key: 'growth-first-scene' }),
    call(6, 'save_member_activity', { ...firstStage, date: '2026-11-21', key: 'growth-first-stage' }),
    call(7, 'save_member_activity', { ...basic, date: '2026-11-22', key: 'growth-next-scene' }),
    call(8, 'get_cast_context', { as_of: '2026-11-22', member_ids: ['12'] }),
    call(9, 'get_cast_context', { as_of: '2026-11-22', discussion_group: '观望组' }),
    call(10, 'get_cast_context', { as_of: '2026-11-22', discussion_group: '质疑组' }),
    call(11, 'get_cast_context', { as_of: '2026-11-22', scope: 'live', member_ids: ['12'] }),
  ]);
  assert.equal(decode(grouped12).discussion_group, '观望组');
  assert.equal(decode(grouped14).discussion_group, '质疑组');
  assert.equal(decode(grouped37).discussion_group, '信随组');
  assert.equal(decode(updatedVoice).discussion_group, '观望组');
  assert.equal(decode(firstAppearance).speaking_move, 'basic_question');
  assert.equal(decode(firstProgress).learner_stage, 'newcomer');
  assert.equal(decode(secondAppearance).speaking_move, 'basic_question');
  const newcomer = decode(laterBasic).members[0].growth;
  assert.equal(newcomer.current_stage, 'newcomer');
  assert.equal(newcomer.review_repeated_basic_questions, true);
  assert.ok(newcomer.distinct_appearance_scenes >= 2);
  assert.deepEqual(decode(watched).members.map(({ profile }) => profile.member_id), ['12']);
  assert.deepEqual(decode(skeptical).members.map(({ profile }) => profile.member_id), ['14']);
  assert.equal(decode(liveGrowth).members[0].growth.current_stage, null);
  const laterProgress = { ...firstStage, date: monday, key: 'growth-next-stage', learner_stage: 'participating',
    summary: '自己指出回落后量变小，再提出收盘条件。' };
  const [observed, movedStage, newView, oldViewAfter, wrongKind] = run([
    call(1, 'save_member_activity', { ...basic, date: monday, key: 'growth-observation',
      speaking_move: 'observation', summary: '先说观察到回落时量变小。' }),
    call(2, 'save_member_activity', laterProgress),
    call(3, 'get_cast_context', { as_of: monday, member_ids: ['12'] }),
    call(4, 'get_cast_context', { as_of: '2026-11-22', member_ids: ['12'] }),
    call(5, 'save_member_activity', { ...basic, date: monday, key: 'bad-stage-kind', learner_stage: 'independent' }),
  ]);
  assert.equal(decode(observed).speaking_move, 'observation');
  assert.equal(decode(movedStage).learner_stage, 'participating');
  assert.equal(decode(newView).members[0].growth.current_stage, 'participating');
  assert.equal(decode(newView).members[0].growth.review_repeated_basic_questions, false);
  assert.equal(decode(oldViewAfter).members[0].growth.current_stage, 'newcomer');
  assert.equal(wrongKind.result.isError, true);
  const legacyPhase = { ...phase, id: 'legacy-fixture' };
  const legacyId = 'adopted-00000000-0000-4000-8000-000000000001';
  writeFileSync(join(project, '.westcott-state', `${legacyId}.json`), JSON.stringify({
    ...linked(formation), phase_id: legacyPhase.id, planned_task: formation, id: legacyId, kind: 'adopted',
    created_at: '2026-11-18T18:00:00Z', approval: '上一版本的测试采用',
  }));
  const [legacyPlan, legacyRestart] = run([
    call(1, 'save_agenda', { ...agenda, expected_revision: 2, phase: legacyPhase }),
    call(2, 'get_agenda', {}),
  ]);
  assert.equal(decode(legacyPlan).tasks[0].adopted_record_id, legacyId);
  assert.equal(decode(legacyRestart).next_task.id, philosophy.id);
  const [correctedLegacyDraft] = run([call(1, 'save_draft', { ...linked(formation), phase_id: legacyPhase.id,
    lesson_scope: 'full_lesson', body: '旧课稿核对后的完整修订。' })]);
  const [correctedLegacyAdoption] = run([call(1, 'record_adoption', {
    id: decode(correctedLegacyDraft).id, approval: '采用核过的修订整课稿。' })]);
  const confirmRevision = { id: decode(correctedLegacyAdoption).id, confirmation: '确认整课定稿更换为修订版。' };
  const [unconfirmedReplacement, confirmedReplacement, subsequentAgenda] = run([
    call(1, 'complete_lesson', confirmRevision),
    call(2, 'complete_lesson', { ...confirmRevision, supersedes: legacyId }),
    call(3, 'get_agenda', {}),
  ]);
  assert.match(unconfirmedReplacement.result.content[0].text, /supersedes/);
  assert.equal(decode(confirmedReplacement).adopted_record_id, confirmRevision.id);
  assert.equal(decode(subsequentAgenda).tasks[0].adopted_record_id, confirmRevision.id);
  assert.equal(decode(subsequentAgenda).next_task.id, philosophy.id);
  console.log('PASS: 29 MCP tools; saved progress across restarts, course records, scoped edits, focused reads, market opening gates, and news/image checks.');
} finally {
  rmSync(project, { recursive: true, force: true });
}
