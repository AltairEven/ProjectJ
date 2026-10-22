import assert from 'node:assert/strict'
import test from 'node:test'
import { classProgress, jobProgress, taskMarks } from '../src/progress.ts'

const at = (task_index, wait_kind = 'answer') => classProgress({ task_index, task_count: 4, wait_kind })

test('the class bar names the goal, task n of N and the closing', () => {
  assert.deepEqual(at(-1, 'goal_confirmation'), { label: 'Start', value: 0, max: 4 })
  assert.deepEqual(at(0), { label: 'Question 1 of 4', value: 1, max: 4 })
  assert.deepEqual(at(3, 'retry'), { label: 'Question 4 of 4', value: 4, max: 4 })
  assert.deepEqual(at(4, 'closing_confirmation'), { label: 'Wrap-up', value: 4, max: 4 })
  // Closing early keeps the bar where the class actually got to.
  assert.deepEqual(at(1, 'closing_confirmation'), { label: 'Wrap-up', value: 2, max: 4 })
  assert.equal(classProgress({ task_index: -1, task_count: 0, wait_kind: 'goal_confirmation' }).max, 1)
})

test('a job bar fills only when the job says how far it is', () => {
  assert.deepEqual(jobProgress('2/5'), { value: 2, max: 5 })
  assert.equal(jobProgress(null), undefined)
  assert.equal(jobProgress('soon'), undefined)
})

test('each task gets a box: ticked once done, marked while current', () => {
  assert.deepEqual(taskMarks(-1, 3), ['todo', 'todo', 'todo'])  // the goal comes first
  assert.deepEqual(taskMarks(1, 3), ['done', 'now', 'todo'])
  assert.deepEqual(taskMarks(3, 3), ['done', 'done', 'done'])  // the closing
  assert.deepEqual(taskMarks(0, 0), [])
})
