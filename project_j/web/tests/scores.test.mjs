import assert from 'node:assert/strict'
import test from 'node:test'
import { recentLine, scoreRows } from '../src/scores.ts'

const objects = [
  { object_id: 'c1', kind: 'course', body: { parent_preview: { story: 'The Paper Bridge (first lesson)' } } },
  { object_id: 'cap', kind: 'capability', body: { dimension_id: 'english.reading.facts', name: 'Reading facts and order' } },
]
const lesson = (activity_id, occurred_at, by_type, by_dimension, unknown = 0) =>
  ({ activity_id, course_ref: { object_id: 'c1', revision: 1 }, occurred_at, by_type, by_dimension, unknown })
const scores = [
  lesson('a1', '2026-10-20T12:00:00Z', { answer: { average: null, count: 0 } }, { 'english.reading.facts': { average: null, count: 0 } }, 2),
  lesson('a2', '2026-10-21T12:00:00Z', { choice: { average: 9, count: 3 }, answer: { average: 6.5, count: 2 } },
    { 'english.reading.facts': { average: 8, count: 5 } }, 1),
]

test('each lesson reads newest first with type names, its story and unknowns apart', () => {
  const rows = scoreRows(scores, objects)
  assert.deepEqual(rows.map(row => row.key), ['a2', 'a1'])
  assert.equal(rows[0].title, 'The Paper Bridge (first lesson)')
  assert.equal(rows[0].types, 'Choose 9.0 (3 questions), Short answer 6.5 (2 questions); 1 question unknown')
  assert.equal(rows[0].dimensions, 'Reading facts and order 8.0 (5 questions)')
  assert.equal(rows[1].types, 'No known scores; 2 questions unknown')  // Never 0.
  assert.match(rows[0].date, /^Oct 2[01], 2026$/)
})

test('the recent line words the lessons the server pooled for planning, by ability', () => {
  const recent = (lessons, average, count) => ({ lessons, by_type: {}, by_dimension: { 'english.reading.facts': { average, count } } })
  assert.equal(recentLine(recent(3, 7.5, 6), objects), 'Last 3 lessons: Reading facts and order 7.5 (6 questions)')
  assert.equal(recentLine(recent(1, null, 0), objects), 'Last 1 lesson: No known scores')  // Never 0.
})
