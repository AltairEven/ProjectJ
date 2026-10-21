import assert from 'node:assert/strict'
import test from 'node:test'
import { recentLine, scoreRows } from '../src/scores.ts'

const objects = [
  { object_id: 'c1', kind: 'course', body: { parent_preview: { story: 'The Paper Bridge（首课）' } } },
  { object_id: 'cap', kind: 'capability', body: { dimension_id: 'english.reading.facts', name: '阅读事实与顺序' } },
]
const lesson = (activity_id, occurred_at, by_type, by_dimension, unknown = 0) =>
  ({ activity_id, course_ref: { object_id: 'c1', revision: 1 }, occurred_at, by_type, by_dimension, unknown })
const scores = [
  lesson('a1', '2026-10-20T12:00:00Z', { answer: { average: null, count: 0 } }, { 'english.reading.facts': { average: null, count: 0 } }, 2),
  lesson('a2', '2026-10-21T12:00:00Z', { choice: { average: 9, count: 3 }, answer: { average: 6.5, count: 2 } },
    { 'english.reading.facts': { average: 8, count: 5 } }, 1),
]

test('each lesson reads newest first with Chinese type names, its story and unknowns apart', () => {
  const rows = scoreRows(scores, objects)
  assert.deepEqual(rows.map(row => row.key), ['a2', 'a1'])
  assert.equal(rows[0].title, 'The Paper Bridge（首课）')
  assert.equal(rows[0].types, '选择 9.0（3题）、精确问答 6.5（2题）；1题未知')
  assert.equal(rows[0].dimensions, '阅读事实与顺序 8.0（5题）')
  assert.equal(rows[1].types, '没有已知分数；2题未知')  // Never 0.
  assert.match(rows[0].date, /^10月2[01]日$/)
})

test('the recent line pools the last five lessons by ability, question by question', () => {
  const more = [...scores, lesson('a3', '2026-10-22T12:00:00Z', {}, { 'english.reading.facts': { average: 5, count: 1 } })]
  assert.equal(recentLine(more, objects), '最近3节课：阅读事实与顺序 7.5（6题）')
  assert.equal(recentLine(scores.slice(0, 1), objects), '最近1节课：没有已知分数')
})
