import assert from 'node:assert/strict'
import test from 'node:test'
import { describe, discussionLines, evaluationLines, modelLines, resourceLines, revisionChanges } from '../src/report.ts'

const doc = (object_id, revision, kind, body) => ({ object_id, revision, kind, body })
const docs = [
  doc('c', 1, 'course', { parent_preview: { story: 'Ben’s Bridge (new material)' } }),
  doc('s', 2, 'state', { dimensions: [{ dimension_id: 'english.reading.facts', judgment: 'needs_help' }] }),
  doc('cap', 1, 'capability', { dimension_id: 'english.reading.facts', name: 'Reading facts and order' }),
  doc('p', 2, 'plan', { main_goal: { description: 'Old goal' } }),
]

test('next-course results read as labelled rows with what each saved object says', () => {
  const rows = new Map(describe({
    next_package: { course_ref: { object_id: 'c', revision: 1 }, start: 'pending', unverified: ['actual_api', 'voice'] },
    adoption: { state_ref: { object_id: 's', revision: 2 }, plan_ref: { object_id: 'p', revision: 1 }, plan_effect: 'reused_with_reason' },
  }, docs))
  assert.equal(rows.get('Lesson'), 'Version 1: Ben’s Bridge (new material)')
  assert.equal(rows.get('Start'), 'Not started')
  assert.equal(rows.get('Not verified'), 'Real API, Voice')
  assert.equal(rows.get('Learning state'), 'Version 2: Reading facts and order: Needs help')
  assert.equal(rows.get('Plan'), 'Version 1 (now version 2)')
  assert.equal(rows.get('Plan change'), 'Kept, with a reason')
})

test('a fast tier binding and its probe item read as Fast', () => {
  const lines = modelLines({
    initialized: true, simulated: false,
    purposes: {
      classroom_judgment: { binding: { adapter: 'codex', model: 'gpt-6.1-sol', effort: 'low', tier: 'fast' }, reasons: [] },
      evaluation: { binding: { adapter: 'codex', model: 'gpt-6.1-sol', effort: 'medium', tier: 'default' }, reasons: [] },
    },
    reports: [{ adapter: 'codex', model: 'gpt-6.1-sol', items: { 'tier:fast': { status: 'unverified' } } }],
  })
  assert.deepEqual(lines, [
    'The model is set up.',
    'Class judgment: codex · gpt-6.1-sol · low · Fast',
    'Evaluation: codex · gpt-6.1-sol · medium',
    'Report codex · gpt-6.1-sol: unverified Fast tier',
  ])
})

test('model status reads as lines without any credential field', () => {
  const ok = modelLines({
    initialized: true, simulated: true,
    purposes: {
      planning: { binding: { adapter: 'simulated', model: 'simulated-jane', effort: 'medium' }, reasons: [] },
      preparation: { binding: { adapter: 'simulated', model: 'simulated-jane', effort: 'medium' }, reasons: [] },
      classroom_judgment: { binding: { adapter: 'simulated', model: 'simulated-jane', effort: 'low' }, reasons: [] },
      evaluation: { binding: { adapter: 'simulated', model: 'simulated-jane', effort: 'medium' }, reasons: [] },
    },
    reports: [{ adapter: 'simulated', model: 'simulated-jane', items: {
      auth: { status: 'verified' }, model: { status: 'verified' }, structured_output: { status: 'verified' },
      cancellation: { status: 'verified' }, 'effort:low': { status: 'verified' }, 'effort:medium': { status: 'verified' },
      tools: { status: 'verified' }, image_input: { status: 'unsupported' }, audio: { status: 'not_probed' } } }],
  })
  assert.deepEqual(ok, [
    'The model is set up (simulated).',
    'Planning: simulated · simulated-jane · medium',
    'Lesson making: simulated · simulated-jane · medium',
    'Class judgment: simulated · simulated-jane · low',
    'Evaluation: simulated · simulated-jane · medium',
    'Report simulated · simulated-jane: verified Sign-in, Model, Structured output, Cancel, Effort low, Effort medium, Tools off; '
      + 'not supported Image input; not probed Audio',
  ])
  const blocked = modelLines({
    initialized: false, simulated: false,
    purposes: { evaluation: { binding: null, reasons: ['No default model'] } },
  })
  assert.equal(blocked[0], 'The model is not set up; lessons, classes, reviews and evaluations cannot run yet.')
  assert.equal(blocked[1], 'Evaluation: Not set; not passed: No default model')
  assert.deepEqual(modelLines(undefined), ['The model status is not available.'])
})

test('an evaluation reads as summary, evidence, unknowns, differences and per-task lines', () => {
  const evaluation = doc('e', 1, 'evaluation', {
    report: { summary: 'Two questions done.', strongest: ['First answer right'], uncertain: [], alignment: 'The offscreen task was not done.' },
    tasks: [{ task_id: 'reading-fact', dimension_id: 'english.reading.facts', working_judgment: 'single_independent', explanation: 'Right at first; no help asked.' }],
    checks: [{ attempt_id: 'a2', task_id: 'reading-sequence', reason: 'Not enough to call it done alone' }],
  })
  const rows = evaluationLines(evaluation, docs)
  assert.deepEqual(rows[0], ['Summary', 'Two questions done.'])
  assert.deepEqual(rows[1], ['Best evidence', 'First answer right'])
  assert.deepEqual(rows[2], ['Not sure yet', 'None'])
  assert.deepEqual(rows[3], ['Lesson vs class', 'The offscreen task was not done.'])
  assert.deepEqual(rows[4], ['Reading facts and order · reading-fact', 'Done alone once: Right at first; no help asked.'])
  assert.deepEqual(rows.at(-1), ['Rule changes', 'reading-sequence: Not enough to call it done alone'])
})

test('a rewritten evaluation shows the replaced summary and each judgment that changed', () => {
  const task = (task_id, working_judgment) => ({ task_id, dimension_id: 'english.reading.facts', working_judgment })
  const before = doc('e', 1, 'evaluation', { report: { summary: 'Both done alone.' }, tasks: [task('reading-fact', 'single_independent'), task('reading-sequence', 'needs_help')] })
  const after = doc('e', 2, 'evaluation', { report: { summary: 'The first one used the answer.' }, tasks: [task('reading-fact', 'unknown'), task('reading-sequence', 'needs_help')] })
  assert.deepEqual(revisionChanges(after, before, docs), [
    ['Earlier summary', 'Both done alone.'],
    ['Reading facts and order · reading-fact', 'Done alone once → Unknown'],
  ])
  assert.deepEqual(revisionChanges(before, before, docs).at(-1), ['Each question', 'No change'])
  assert.deepEqual(revisionChanges(after, doc('e', 1, 'evaluation', { tasks: [] }), docs)[0], ['Earlier summary', 'The earlier version has no written evaluation.'])
})

test('an evaluation saved before model drafts says so', () => {
  assert.deepEqual(evaluationLines(doc('e', 1, 'evaluation', { tasks: [] }), docs), [['Evaluation', 'This version has no written evaluation.']])
})

test('a restored run is labelled without saying simulation', () => {
  const rows = new Map(describe({ result: 'restored_to_new_simulation', run_id: 'restored-1' }, docs))
  assert.equal(rows.get('Result'), 'Restored to a new run folder')
  assert.equal(rows.get('New run folder'), 'restored-1')
})

test('a model-planned demand explains its change and an uncovered goal reads as not ready', () => {
  const planned = [...docs,
    doc('cap2', 1, 'capability', { dimension_id: 'science.observation.report', name: 'Real observation report' }),
    doc('d', 3, 'demand', {
      direction: 'observe', primary_goal: { dimension_id: 'science.observation.report', description: 'Say what really happened to the paper bridge' },
      note: { before: 'Reading facts', after: 'Real observation report', why: 'Both reading questions needed hints', uncertain: ['Recall', 'Transfer'], expectation: 'Report real conditions first' },
    }),
    doc('r', 1, 'review', { conclusion: 'Add real reports first', unknown: ['Time available'], checks: ['The time is fixed at 30 minutes; the change does not apply.'] }),
  ]
  const rows = new Map(describe({
    next_package: { status: 'not_ready', reason: 'The lesson could not be made (the model is not available now).' },
    adoption: { demand_ref: { object_id: 'd', revision: 3 }, plan_effect: 'adjusted', plan_reason: 'Add samples this week' },
    review: { review_ref: { object_id: 'r', revision: 1 } },
  }, planned))
  assert.equal(rows.get('Next lesson plan'), 'Version 3: Short check: Say what really happened to the paper bridge')
  assert.equal(rows.get('Before'), 'Reading facts')
  assert.equal(rows.get('Now'), 'Real observation report')
  assert.equal(rows.get('Why'), 'Both reading questions needed hints')
  assert.equal(rows.get('Not sure yet'), 'Recall; Transfer')
  assert.equal(rows.get('Next lesson'), 'Report real conditions first')
  assert.equal(rows.get('Lesson status'), 'Lesson not ready')
  assert.match(rows.get('Reason'), /^The lesson could not be made/)
  assert.equal(rows.get('Plan change'), 'Changed')
  assert.equal(rows.get('Why the plan changed'), 'Add samples this week')
  assert.equal(rows.get('Still unknown'), 'Time available')
  assert.equal(rows.get('Rule changes'), 'The time is fixed at 30 minutes; the change does not apply.')
})

test('resource lines show stage, spoken words, versions and adoption', () => {
  const demo = { resource_id: 'demo:read-aloud-1', kind: 'audio', purpose: 'Reading model: read-aloud-1', text: 'Mia has paper.', required: true,
    stage: 'help', fallback: 'Needed before the class.', state: 'generated', versions: [{ version: 1 }, { version: 2 }], adopted_version: 1 }
  const lines = resourceLines({ resources: [
    demo,
    { resource_id: 'cup', kind: 'image', purpose: 'Show two cups', required: false, stage: 'help', fallback: 'Use real cups.', state: 'planned' },
    { resource_id: 'clip', kind: 'video', purpose: 'Pouring water', required: false, stage: 'question', fallback: 'Show it yourself.', state: 'not_connected' },
    { resource_id: 'voice:goal', kind: 'audio', purpose: 'Opening', text: 'I am Jane.', required: false, stage: 'question', fallback: 'Made live.',
      state: 'adopted', versions: [{ version: 1 }], adopted_version: 1 },
  ] })
  assert.deepEqual(lines, [
    'Voice (needed, after help): Reading model: read-aloud-1 "Mia has paper."; without it: Needed before the class. - Ready to look at, version 1 in use, 2 in all',
    'Picture (optional, after help): Show two cups; without it: Use real cups. - Listed; made after you confirm',
    'Video (optional, with the question): Pouring water; without it: Show it yourself. - Not connected',
    'Voice (optional, with the question): Opening "I am Jane."; without it: Made live. - In use, version 1 in use, 1 in all',
  ])
  assert.deepEqual(resourceLines({ resources: [] }), [])
})

test('discussion rounds read as the student feedback and what the model changed', () => {
  assert.deepEqual(discussionLines({ discussion: [{ feedback: 'Make question 1 easier.', note: 'Question 1 is more direct now.' }], confirmation: null }), [
    ['Round 1: your note', 'Make question 1 easier.'],
    ['Round 1: what changed', 'Question 1 is more direct now.'],
    ['Confirmed', 'Not yet'],
  ])
  const [[label, value]] = discussionLines({ discussion: [], confirmation: { state: 'confirmed', confirmed_at: '2026-10-19T12:00:00+00:00' } })
  assert.equal(label, 'Confirmed')
  assert.match(value, /^Yes \(Oct 19, 2026, \d{1,2}:00 [AP]M\)$/)
})

test('a drafted course in the next-course report reads as waiting for confirmation', () => {
  assert.equal(new Map(describe({ next_package: { preparation: 'drafted' } }, docs)).get('Lesson making'), 'Written, waiting for you')
})

test("an evaluation lists each question's score and reason", async () => {
  const { evaluationLines, scoreText } = await import('../src/report.ts')
  const evaluation = { object_id: 'e', revision: 1, kind: 'evaluation', body: {
    report: { summary: 'Summary', strongest: [], uncertain: [], alignment: 'None' },
    tasks: [{ task_id: 'q1', dimension_id: 'english.reading.facts', working_judgment: 'unknown', explanation: 'Note' }],
    scores: [
      { task_id: 'q1', dimension_id: 'english.reading.facts', score: 7, basis: 'rule', reason: 'Right after 1 wrong try, 1 hint used' },
      { task_id: 'r1', dimension_id: 'english.speaking.pronunciation', score: 8.5, basis: 'pronunciation', reason: 'From the last scored reading' },
      { task_id: 'q2', dimension_id: 'english.reading.facts', score: null, basis: 'model', reason: 'Not finished when the class stopped' },
    ],
  } }
  const rows = evaluationLines(evaluation, docs)
  assert.deepEqual(rows.slice(-3), [
    ['Score · Reading facts and order · q1', '7/10 (Right after 1 wrong try, 1 hint used)'],
    ['Score · english.speaking.pronunciation · r1', '8.5/10 (From the last scored reading; the score limits are not calibrated)'],
    ['Score · Reading facts and order · q2', 'Unknown (Not finished when the class stopped)'],
  ])
  assert.equal(scoreText({ score: 0, basis: 'rule', reason: 'Not right after all the help' }), '0/10 (Not right after all the help)')
  const old = { ...evaluation, body: { ...evaluation.body, scores: undefined } }
  assert.ok(!evaluationLines(old, docs).some(([label]) => label.startsWith('Score')))
})
