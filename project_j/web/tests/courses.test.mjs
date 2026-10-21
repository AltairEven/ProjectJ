import assert from 'node:assert/strict'
import test from 'node:test'
import { courseRows, latestFact, needsMaterials, retakeOffered } from '../src/courses.ts'

const ref = (object_id, revision) => ({ object_id, revision })
const course = (id, revision, created_at, story) => ({ object_id: id, revision, kind: 'course', created_at,
  body: { parent_preview: { story }, tasks: [{ task_id: 't', purpose: 'reading' }] } })
const preview = (id, target, confirmed) => ({ object_id: id, revision: 1, kind: 'preview', created_at: '',
  body: { course_ref: target, ...(confirmed ? { confirmation: { course_ref: target } } : {}) } })
const fact = (activity, target) => ({ object_id: `f-${activity}`, revision: 1, kind: 'fact', created_at: '', body: { activity_id: activity, course_ref: target } })
const evaluation = activity => ({ object_id: `e-${activity}`, revision: 1, kind: 'evaluation', created_at: '', body: { activity_id: activity } })

test('each course has one row, newest first, with its state from the saved objects', () => {
  const objects = [
    course('c1', 1, '2026-10-01', '已评价的课'), preview('p1', ref('c1', 1), true), fact('a1', ref('c1', 1)), evaluation('a1'),
    course('c2', 1, '2026-10-02', '上过的课'), preview('p2', ref('c2', 1), true), fact('a2', ref('c2', 1)),
    course('c3', 2, '2026-10-03', '确认过的课'), preview('p3', ref('c3', 2), true),
    course('c4', 3, '2026-10-04', '等确认的课'), preview('p4', ref('c4', 3), false),
    course('c5', 2, '2026-10-05', '还在改的课'), preview('p5', ref('c5', 1), false),
  ]
  assert.deepEqual(courseRows(objects).map(row => [row.object_id, row.revision, row.title, row.state]), [
    ['c5', 2, '还在改的课', '备课中'],
    ['c4', 3, '等确认的课', '等你确认'],
    ['c3', 2, '确认过的课', '待上课'],
    ['c2', 1, '上过的课', '已上'],
    ['c1', 1, '已评价的课', '已评价'],
  ])
})

test('only a course with an offscreen task needs the student to have the materials ready', () => {
  assert.equal(needsMaterials({ body: { tasks: [{ purpose: 'reading' }, { purpose: 'offscreen' }] } }), true)
  assert.equal(needsMaterials({ body: { tasks: [{ purpose: 'reading' }] } }), false)
})

test('only the course the main page offers again can be retaken', () => {
  const next = { kind: 'evaluation', subject: { activity_id: 'a1', retake: ref('c1', 2) } }
  assert.equal(retakeOffered(next, course('c1', 2, '', 'x')), true)
  assert.equal(retakeOffered(next, course('c1', 1, '', 'x')), false)
  assert.equal(retakeOffered(next, course('c2', 2, '', 'x')), false)
  assert.equal(retakeOffered({ kind: 'evaluation', subject: { activity_id: 'a1' } }, course('c1', 2, '', 'x')), false)
  assert.equal(retakeOffered(undefined, course('c1', 2, '', 'x')), false)
})

test('a course taken again shows its latest class', () => {
  const stopped = { ...fact('a1', ref('c1', 2)), created_at: '2026-10-20T15:58:01Z' }
  const retaken = { ...fact('a2', ref('c1', 2)), created_at: '2026-10-21T09:00:00Z' }
  const other = { ...fact('a3', ref('c2', 1)), created_at: '2026-10-22T09:00:00Z' }
  assert.equal(latestFact(course('c1', 2, '', 'x'), [retaken, other, stopped])?.body.activity_id, 'a2')
  assert.equal(latestFact(course('c1', 2, '', 'x'), [stopped, other, retaken])?.body.activity_id, 'a2')
  assert.equal(latestFact(course('c9', 1, '', 'x'), [stopped]), undefined)
})
