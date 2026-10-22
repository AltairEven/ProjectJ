import assert from 'node:assert/strict'
import test from 'node:test'
import { dimensionLines, evaluationRows, goalCard, byNewest } from '../src/learning.ts'

const ref = (object_id, revision) => ({ object_id, revision })
const doc = (kind, object_id, revision, body, extra = {}) => ({ kind, object_id, revision, body, created_at: '', source_revision: 1, ...extra })
const catalog = [doc('capability', 'cap1', 1, { dimension_id: 'english.reading.facts', name: 'Reading facts and order' })]

test('a dimension without evidence reads as unknown, with what is missing', () => {
  const state = doc('state', 's', 2, { dimensions: [
    { dimension_id: 'english.reading.facts', judgment: 'single_independent', evidence_refs: [ref('f', 1)], help: 'none', material_condition: 'new material', limitations: [] },
    { dimension_id: 'english.speaking.report', judgment: 'unknown', help: 'unknown', material_condition: 'not observed', limitations: ['No real sample yet.'] },
  ] })
  const [reading, speaking] = dimensionLines(state, catalog)
  assert.equal(reading.name, 'Reading facts and order')
  assert.equal(reading.judgment, 'Done alone once')
  assert.match(reading.basis, /^From 1 class record,/)
  assert.match(reading.basis, /no help/)
  assert.deepEqual([speaking.name, speaking.judgment, speaking.basis], ['english.speaking.report', 'Unknown', 'No real sample yet.'])
})

test('each lesson has an evaluation row; only a current version can be acknowledged', () => {
  const fact = (activity, revision) => doc('fact', `f-${activity}`, revision, { activity_id: activity, course_ref: ref('c', 1) }, { created_at: activity })
  const evaluation = (activity, source_revision) => doc('evaluation', `e-${activity}`, 1, { activity_id: activity }, { source_revision })
  const feedback = (activity, objection) => doc('feedback', `fb-${activity}`, 1, { activity_id: activity, corrections: objection ? [{ note: 'Not right' }] : [] })
  const objects = [
    fact('a1', 1),                                        // not evaluated yet
    fact('a2', 1), evaluation('a2', 1),                   // waiting for the student
    fact('a3', 1), evaluation('a3', 1), doc('report', 'k-a3', 1, { activity_id: 'a3', acknowledged_ref: ref('e-a3', 1) }),
    fact('a4', 1), evaluation('a4', 2), feedback('a4', true), // re-evaluated after an objection
    fact('a5', 1), evaluation('a5', 1), feedback('a5', false), // a note arrived after this version: stale
  ]
  const rows = new Map(evaluationRows(objects).map(row => [row.activity_id, row]))
  assert.deepEqual([...rows.keys()], ['a5', 'a4', 'a3', 'a2', 'a1'])
  assert.deepEqual(rows.get('a1'), { activity_id: 'a1', state: 'Evaluating' })
  assert.deepEqual(rows.get('a2'), { activity_id: 'a2', state: 'Waiting for you', evaluation_ref: ref('e-a2', 1) })
  assert.equal(rows.get('a3').state, 'Acknowledged')
  assert.deepEqual(rows.get('a4'), { activity_id: 'a4', state: 'Disputed', evaluation_ref: ref('e-a4', 1) })
  assert.deepEqual(rows.get('a5'), { activity_id: 'a5', state: 'Evaluating' })
})

test('a goal card names the goals with their capability, the reason and new capabilities', () => {
  const demand = doc('demand', 'd', 3, {
    primary_goal: { dimension_id: 'english.reading.facts', description: 'Follow the order of events' },
    auxiliary_goal: { dimension_id: 'english.listening.gist', description: 'Get the main idea' },
    goal_reason: 'Reading is steady; add listening.',
    proposed_capabilities: [{ dimension_id: 'english.listening.gist', name: 'Listening for the main idea', definition: 'Listen and say the main idea' }],
  })
  assert.deepEqual(goalCard(demand, catalog), {
    primary: 'Reading facts and order: Follow the order of events',
    auxiliary: 'Listening for the main idea: Get the main idea',
    reason: 'Reading is steady; add listening.',
    newCapabilities: ['Listening for the main idea: Listen and say the main idea'],
  })
  assert.equal(goalCard(doc('demand', 'd', 1, { primary_goal: { dimension_id: 'x', description: 'y' }, auxiliary_goal: { dimension_id: 'x', description: 'z' } }), catalog).reason, 'None')
})

test('newest first, and a document without a time sorts last', () => {
  const docs = [{ created_at: '2026-10-20T01:00:00Z' }, {}, { created_at: '2026-10-21T01:00:00Z' }]
  assert.deepEqual([...docs].sort(byNewest), [docs[2], docs[0], docs[1]])
})
