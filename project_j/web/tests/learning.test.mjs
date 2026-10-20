import assert from 'node:assert/strict'
import test from 'node:test'
import { dimensionLines, evaluationRows, goalCard } from '../src/learning.ts'

const ref = (object_id, revision) => ({ object_id, revision })
const doc = (kind, object_id, revision, body, extra = {}) => ({ kind, object_id, revision, body, created_at: '', source_revision: 1, ...extra })
const catalog = [doc('capability', 'cap1', 1, { dimension_id: 'english.reading.facts', name: '阅读事实与顺序' })]

test('a dimension without evidence reads as unknown, with what is missing', () => {
  const state = doc('state', 's', 2, { dimensions: [
    { dimension_id: 'english.reading.facts', judgment: 'single_independent', evidence_refs: [ref('f', 1)], help: 'none', material_condition: '新材料', limitations: [] },
    { dimension_id: 'english.speaking.report', judgment: 'unknown', help: 'unknown', material_condition: '尚未观察', limitations: ['没有实际样本。'] },
  ] })
  const [reading, speaking] = dimensionLines(state, catalog)
  assert.equal(reading.name, '阅读事实与顺序')
  assert.equal(reading.judgment, '单次独立')
  assert.match(reading.basis, /1条/)
  assert.match(reading.basis, /无帮助/)
  assert.deepEqual([speaking.name, speaking.judgment, speaking.basis], ['english.speaking.report', '未知', '没有实际样本。'])
})

test('each lesson has an evaluation row; only a current version can be acknowledged', () => {
  const fact = (activity, revision) => doc('fact', `f-${activity}`, revision, { activity_id: activity, course_ref: ref('c', 1) }, { created_at: activity })
  const evaluation = (activity, source_revision) => doc('evaluation', `e-${activity}`, 1, { activity_id: activity }, { source_revision })
  const feedback = (activity, objection) => doc('feedback', `fb-${activity}`, 1, { activity_id: activity, corrections: objection ? [{ note: '不对' }] : [] })
  const objects = [
    fact('a1', 1),                                        // not evaluated yet
    fact('a2', 1), evaluation('a2', 1),                   // waiting for the student
    fact('a3', 1), evaluation('a3', 1), doc('report', 'k-a3', 1, { activity_id: 'a3', acknowledged_ref: ref('e-a3', 1) }),
    fact('a4', 1), evaluation('a4', 2), feedback('a4', true), // re-evaluated after an objection
    fact('a5', 1), evaluation('a5', 1), feedback('a5', false), // a note arrived after this version: stale
  ]
  const rows = new Map(evaluationRows(objects).map(row => [row.activity_id, row]))
  assert.deepEqual([...rows.keys()], ['a5', 'a4', 'a3', 'a2', 'a1'])
  assert.deepEqual(rows.get('a1'), { activity_id: 'a1', state: '评价中' })
  assert.deepEqual(rows.get('a2'), { activity_id: 'a2', state: '等你认可', evaluation_ref: ref('e-a2', 1) })
  assert.equal(rows.get('a3').state, '已认可')
  assert.deepEqual(rows.get('a4'), { activity_id: 'a4', state: '有异议', evaluation_ref: ref('e-a4', 1) })
  assert.deepEqual(rows.get('a5'), { activity_id: 'a5', state: '评价中' })
})

test('a goal card names the goals with their capability, the reason and new capabilities', () => {
  const demand = doc('demand', 'd', 3, {
    primary_goal: { dimension_id: 'english.reading.facts', description: '读懂事件顺序' },
    auxiliary_goal: { dimension_id: 'english.listening.gist', description: '听懂大意' },
    goal_reason: '阅读已稳定，加入听力。',
    proposed_capabilities: [{ dimension_id: 'english.listening.gist', name: '听懂大意', definition: '听一段话说出主要意思' }],
  })
  assert.deepEqual(goalCard(demand, catalog), {
    primary: '阅读事实与顺序：读懂事件顺序',
    auxiliary: '听懂大意：听懂大意',
    reason: '阅读已稳定，加入听力。',
    newCapabilities: ['听懂大意：听一段话说出主要意思'],
  })
  assert.equal(goalCard(doc('demand', 'd', 1, { primary_goal: { dimension_id: 'x', description: 'y' }, auxiliary_goal: { dimension_id: 'x', description: 'z' } }), catalog).reason, '无')
})
