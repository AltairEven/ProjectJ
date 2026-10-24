import assert from 'node:assert/strict'
import test from 'node:test'
import { entryRows, markedInLesson, statusLabel } from '../src/vocabulary.ts'

test('final marks follow the event order, case-insensitively', () => {
  const events = [
    { kind: 'word_marked', payload: { word: 'Car' } }, { kind: 'word_marked', payload: { word: 'fold' } },
    { kind: 'word_unmarked', payload: { word: 'car' } }, { kind: 'input', payload: { text: 'car' } },
  ]
  assert.deepEqual(markedInLesson(events), ['fold'])
})

test('status labels: getting ready, not ready, ready', () => {
  assert.equal(statusLabel({ status: 'marked', definition: null }), 'Getting ready…')
  assert.equal(statusLabel({ status: 'pending', definition: null }), 'Getting ready…')
  assert.equal(statusLabel({ status: 'failed', definition: null }), 'Not ready (try again)')
  assert.equal(statusLabel({ status: 'active', definition: null }), 'Not ready (try again)')
  assert.equal(statusLabel({ status: 'ready', definition: 'a way over' }), 'Ready')
  assert.equal(statusLabel({ status: 'active', definition: 'a way over' }), 'Ready')
})

test('entry rows show every recorded attribute of a word', () => {
  const rows = new Map(entryRows({
    lemma: 'fold', forms: ['folds', 'fold'], part_of_speech: 'verb', definition: 'to bend', status: 'mastered', weight: 3,
    mark_count: 2, exit_count: 5, first_marked: '2026-10-01', last_marked: '2026-10-10', last_seen: '2026-11-14',
    mastered_activity: 'act-4', marks: [{ course_title: 'Paper', sentence: 'She folds it.', date: '2026-10-01' }],
    unmarked_appearances: ['Lesson 1', 'Lesson 2'],
  }))
  assert.equal(rows.get('Word forms'), 'folds, fold')
  assert.equal(rows.get('Part of speech'), 'verb')
  assert.equal(rows.get('Meaning'), 'to bend')
  assert.equal(rows.get('Status'), 'Mastered on 2026-11-14 (Lesson 2)')
  assert.equal(rows.get('Times marked'), 2)
  assert.equal(rows.get('Seen without marking'), '5 (Lesson 1, Lesson 2)')
  assert.equal(rows.get('Weight'), 3)
  assert.equal(rows.get('Marked in'), 'Paper (2026-10-01): She folds it.')
  assert.equal(rows.get('First marked'), '2026-10-01')
  assert.equal(rows.get('Last seen'), '2026-11-14')
})
