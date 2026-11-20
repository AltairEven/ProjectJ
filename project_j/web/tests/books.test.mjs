import assert from 'node:assert/strict'
import test from 'node:test'
import { bookLine, booksOf, expandBlocked, levelled } from '../src/books.ts'

const row = (extra = {}) => ({
  book_id: 'b', course_ref: { object_id: 'c1', revision: 2 }, title: null, target: null, words: 0,
  chapters: [], planned: null, state: 'writing', reason: null, ...extra,
})
const dash = (extra = {}) => ({ jobs: [], active: null, ...extra })

test('book lines say where each book stands', () => {
  assert.equal(bookLine(row()), 'Planning')
  assert.equal(bookLine(row({ planned: 5, chapters: ['a', 'b'] })), 'Writing chapter 3 of 5')
  assert.equal(bookLine(row({ state: 'drawing', planned: 4 })), 'Drawing pictures')
  assert.equal(bookLine(row({ state: 'revising', title: 'T', revising: 2 })), 'T · Rewriting chapter 2')
  assert.equal(bookLine(row({ state: 'revising', title: 'T' })), 'T · Rewriting a chapter')
  assert.equal(bookLine(row({ state: 'failed', reason: 'stopped because a class started' })),
    'Failed: stopped because a class started')
  const ready = row({ state: 'ready', title: 'T', target: '600L', words: 3120, chapters: ['1', '2', '3', '4', '5'] })
  assert.equal(bookLine(ready), 'T · 600L · 3,120 words · 5 chapters')
  assert.equal(bookLine({ ...ready, target: null }), 'T · 3,120 words · 5 chapters')
})

test('a course page lists only the books of that version', () => {
  const books = [row({ book_id: 'a' }), row({ book_id: 'b', course_ref: { object_id: 'c1', revision: 1 } }), row({ book_id: 'c', course_ref: { object_id: 'c9', revision: 2 } })]
  assert.deepEqual(booksOf({ object_id: 'c1', revision: 2 }, books).map(book => book.book_id), ['a'])
})

test('expanding waits for a class, a book or the check', () => {
  assert.equal(expandBlocked(dash()), null)
  assert.ok(expandBlocked(dash({ jobs: [{ name: 'reading:x', state: 'running' }] })))
  assert.ok(expandBlocked(dash({ jobs: [{ name: 'reference', state: 'running' }] })))
  assert.ok(expandBlocked(dash({ active: { activity_id: 'a' } })))
  assert.equal(expandBlocked(dash({ jobs: [{ name: 'reading:x', state: 'failed' }, { name: 'evaluate:a', state: 'running' }] })), null)
})

test('without a Lexile there is no difficulty to choose', () => {
  assert.equal(levelled({}), false)
  assert.equal(levelled({ same: '500L', little: '550L' }), true)
})
