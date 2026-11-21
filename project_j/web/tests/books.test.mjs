import assert from 'node:assert/strict'
import test from 'node:test'
import { bookLine, booksOf, bookTitle, bookView, changesLine, coverHue, coverOf, draftActions, expandBlocked, levelled, missingLine, neighbours, outlineLine, shelfItem } from '../src/books.ts'

const row = (extra = {}) => ({
  book_id: 'b', course_ref: { object_id: 'c1', revision: 2 }, title: null, target: null, words: 0,
  chapters: [], planned: null, state: 'writing', reason: null, saved: null, draft: false, changes: null, ...extra,
})
const SAVED = { title: 'T', words: 1, chapters: 1 }
const dash = (extra = {}) => ({ jobs: [], active: null, ...extra })

test('book lines say where each book stands', () => {
  assert.equal(bookLine(row({ planned: 5, chapters: ['a', 'b'] })), 'Writing chapter 3 of 5')
  assert.equal(bookLine(row({ state: 'drawing', planned: 4 })), 'Drawing pictures')
  assert.equal(bookLine(row({ state: 'revising', title: 'T', revising: 2 })), 'Rewriting chapter 2')
  assert.equal(bookLine(row({ state: 'revising', title: 'T' })), 'Rewriting a chapter')
  assert.equal(bookLine(row({ state: 'covering', title: 'T' })), 'Redrawing the cover')
  assert.equal(bookLine(row({ state: 'failed', reason: 'stopped because a class started' })),
    'Failed: stopped because a class started')
  const ready = row({ state: 'ready', title: 'T', target: '600L', words: 3120, chapters: ['1', '2', '3', '4', '5'] })
  assert.equal(bookLine(ready), '600L · 3,120 words · 5 chapters')  // the title is shown apart
  assert.equal(bookLine({ ...ready, target: null }), '3,120 words · 5 chapters')
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

test('outline states read as a discussion', () => {
  const outline = { title: 'T', summary: 'S', characters: [], style: 's', chapters: [{ title: 'A', plot: 'p', words: [] }], pictures: [] }
  assert.equal(bookLine(row({ state: 'outlining', outline, version: 2, target: '269L' })), 'Outline, version 2')
  assert.equal(bookTitle(row({ state: 'outlining', outline, version: 1 })), 'T')  // its outline names it
  assert.equal(bookLine(row({ state: 'outlining' })), 'Planning the outline')
  assert.equal(outlineLine(row({ state: 'outlining', outline, version: 2 }), false), 'Outline, version 2')
  assert.equal(outlineLine(row({ state: 'outlining' }), true), 'Planning the outline')
  assert.equal(outlineLine(row({ state: 'ready', outline, continuing: true, version: 1 }), true), 'Planning the outline')
  assert.equal(outlineLine(row({ state: 'ready', outline, continuing: true, version: 1 }), false), 'Outline, version 1')
  assert.equal(outlineLine(row({ state: 'ready', outline }), true), 'Planning the outline')
  assert.equal(outlineLine(row({ state: 'ready', outline }), false), null)
  assert.equal(outlineLine(row({ state: 'revising', outline }), true), null)
})

test('a never-saved book is greyed in Read and opens for editing in Edit', () => {
  const book = row({ state: 'writing', planned: 5, chapters: ['a'], draft: true })
  assert.deepEqual(shelfItem(book, 'read'), { line: 'Writing chapter 2 of 5 · Not saved yet', opens: null, unsaved: false })
  const written = row({ state: 'ready', words: 1620, chapters: ['a', 'b'], draft: true })  // written, waiting for its first save
  assert.equal(shelfItem(written, 'read').line, '1,620 words · 2 chapters · Not saved yet')
  assert.deepEqual(shelfItem(book, 'edit'), { line: 'Writing chapter 2 of 5', opens: 'edit', unsaved: true })
})

test('a saved book opens the reader (with its download) and shows its saved line', () => {
  const book = row({ state: 'ready', title: 'The Paper Bridge', target: '269L', words: 900, chapters: ['1', '2', '3'], draft: true,
    saved: { title: 'The Paper Bridge', words: 2094, chapters: 5 } })
  assert.deepEqual(shelfItem(book, 'read'), { line: '269L · 2,094 words · 5 chapters', opens: 'read', unsaved: false })
  assert.deepEqual(shelfItem(book, 'edit'), { line: '269L · 900 words · 3 chapters', opens: 'edit', unsaved: true })
  assert.equal(bookTitle(book), 'The Paper Bridge')
  assert.equal(bookTitle(row()), 'New book')
})

test('unsaved changes are listed, or none yet', () => {
  assert.equal(changesLine({ rewritten: [2], added: 1, drawn: 2 }), 'chapter 2 rewritten, 1 new chapter, 2 new pictures')
  assert.equal(changesLine({ rewritten: [2, 3], added: 2, drawn: 1 }), 'chapters 2, 3 rewritten, 2 new chapters, 1 new picture')
  assert.equal(changesLine({ rewritten: [], added: 0, drawn: 0 }), 'No changes yet')
  assert.equal(changesLine({ rewritten: [], added: 0, drawn: 0, cover: true }), 'new cover')
  assert.equal(changesLine({ rewritten: [2], added: 0, drawn: 1, cover: true }), 'chapter 2 rewritten, 1 new picture, new cover')
})

test('save, discard and preview show and work only when they apply', () => {
  assert.equal(draftActions(row({ state: 'ready' })), null)  // no draft
  assert.deepEqual(draftActions(row({ state: 'ready', continuing: true, draft: true, chapters: ['a'], saved: SAVED })),
    { reason: 'Write or discard the new chapters first', revert: true, preview: true })
  assert.deepEqual(draftActions(row({ state: 'writing', draft: true })), { reason: 'Wait until it is written', revert: false, preview: false })
  assert.equal(draftActions(row({ state: 'revising', draft: true, chapters: ['a'] })).reason, 'Wait until the chapter is rewritten')
  assert.equal(draftActions(row({ state: 'covering', draft: true, chapters: ['a'] })).reason, 'Wait until the cover is drawn')
  assert.deepEqual(draftActions(row({ state: 'ready', draft: true, chapters: ['a'] })), { reason: null, revert: false, preview: true })
})

test('the reader stops at the first and the last chapter', () => {
  assert.deepEqual(neighbours(0, 3), { previous: null, next: 1 })
  assert.deepEqual(neighbours(1, 3), { previous: 0, next: 2 })
  assert.deepEqual(neighbours(2, 3), { previous: 1, next: null })
  assert.deepEqual(neighbours(0, 1), { previous: null, next: null })
})

test('a bookshelf address names its view and book', () => {
  const book = 'lesson-001-2026-10-24-20261120T081500Z'
  assert.deepEqual(bookView(`edit:${book}`), { mode: 'edit', book })
  assert.deepEqual(bookView(`draft:${book}`), { mode: 'draft', book })
  assert.deepEqual(bookView(`read:${book}`), { mode: 'read', book })
  assert.equal(bookView('look:x'), null)
  assert.equal(bookView('read:'), null)
  assert.equal(bookView(undefined), null)
})

test("the plain cover's colour is fixed per book", () => {
  const a = 'lesson-001-2026-10-24-20261119T120000Z', b = 'lesson-001-2026-10-24-20261120T081500Z'
  assert.equal(coverHue(a), coverHue(a))
  for (const id of [a, b, '']) assert.ok(Number.isInteger(coverHue(id)) && coverHue(id) >= 0 && coverHue(id) < 360)
  assert.notEqual(coverHue(a), coverHue(b))
  // Books of one lesson differ only in the time they were started: their colours must still be far apart.
  const shelf = ['20261119T120000Z', '20261120T081500Z', '20261120T093000Z', '20261120T090000Z', '20261120T101500Z', '20261120T110000Z']
    .map(time => coverHue(`lesson-001-2026-10-24-${time}`))
  for (const [i, x] of shelf.entries()) for (const y of shelf.slice(i + 1)) {
    const gap = Math.abs(x - y)
    assert.ok(Math.min(gap, 360 - gap) >= 15, `${x}° and ${y}° look alike`)
  }
})

test('each mode shows its own cover version', () => {
  const edited = row({ saved: { ...SAVED, cover: 1 }, cover: 2, draft: true })
  assert.deepEqual(coverOf(edited, 'read'), { draft: false, version: 1 })
  assert.deepEqual(coverOf(edited, 'edit'), { draft: true, version: 2 })
  assert.deepEqual(coverOf(row({ saved: { ...SAVED, cover: 1 }, cover: 1 }), 'edit'), { draft: false, version: 1 })
  assert.deepEqual(coverOf({ ...edited, cover: 3 }, 'edit'), { draft: true, version: 3 })  // redrawn: fetched again
  assert.equal(coverOf(row({ saved: SAVED }), 'read'), null)
  assert.equal(coverOf(row({ saved: { ...SAVED, cover: null }, cover: null, draft: true }), 'edit'), null)
  assert.equal(coverOf(row(), 'read'), null)
})

test('missing pictures say how many and why the last one was not drawn', () => {
  assert.equal(missingLine(row({ state: 'ready', missing_pictures: 0 })), null)
  assert.equal(missingLine(row({ state: 'ready', missing_pictures: 1 })), '1 picture not drawn')
  assert.equal(missingLine(row({ state: 'ready', missing_pictures: 3, picture_failure: 'the safety system blocked the picture' })),
    '3 pictures not drawn: the safety system blocked the picture')
})
