import assert from 'node:assert/strict'
import test from 'node:test'
import { answerMode, emptyAnswer, picturesSettled, fillParts, fillWord, ready, setBlank, tapCard, tapPair, toResponse } from '../src/tasks.ts'

const choice = { type: 'choice', version: 1, variant: 'single', options: ['a', 'b', 'c'] }
const order = { type: 'order', version: 1, variant: 'events', items: ['B', 'A', 'C'] }
const match = { type: 'match', version: 1, left: ['bends', 'folds', 'smiles'], right: ['makes a crease', 'falls down', 'is not straight', 'looks happy'] }
const bank = { type: 'fill', version: 1, variant: 'word_bank', text: 'She ___ her car on the ___.', blanks: 2, bank: ['paper', 'folds', 'puts'] }
const free = { type: 'fill', version: 1, variant: 'free', text: 'She ___ her car.', blanks: 1, bank: null }

test('a choice is ready once an option is picked and answers by its display position', () => {
  const answer = emptyAnswer(choice)
  assert.equal(ready(choice, answer), false)
  const picked = { ...answer, choice: 2 }
  assert.equal(ready(choice, picked), true)
  assert.deepEqual(toResponse(picked), { choice: 2 })
})

test('cards go into a row in tap order and a placed card comes back when tapped', () => {
  let answer = emptyAnswer(order)
  for (const index of [1, 0]) answer = tapCard(answer, index)
  assert.deepEqual(answer.order, [1, 0])
  assert.equal(ready(order, answer), false)
  answer = tapCard(answer, 1)  // Taken back.
  assert.deepEqual(answer.order, [0])
  for (const index of [1, 2]) answer = tapCard(answer, index)
  assert.equal(ready(order, answer), true)
  assert.deepEqual(toResponse(answer), { order: [0, 1, 2] })
})

test('a right-hand item pairs with one left item; pairing it again moves it', () => {
  let answer = emptyAnswer(match)
  assert.equal(tapPair(answer, 'right', 0), answer)  // No left item picked yet.
  answer = tapPair(tapPair(answer, 'left', 0), 'right', 2)
  assert.deepEqual(answer.pairs, [2, null, null])
  answer = tapPair(tapPair(answer, 'left', 1), 'right', 2)  // The same right item moves.
  assert.deepEqual(answer.pairs, [null, 2, null])
  answer = tapPair(answer, 'left', 1)  // Tapping a paired left item unpairs it.
  assert.deepEqual(answer.pairs, [null, null, null])
  for (const [left, right] of [[0, 2], [1, 0], [2, 3]]) answer = tapPair(tapPair(answer, 'left', left), 'right', right)
  assert.equal(ready(match, answer), true)
  assert.deepEqual(toResponse(answer), { pairs: [2, 0, 3] })
})

test('bank words fill the next empty blank; free blanks are typed; blanks are trimmed for readiness', () => {
  assert.deepEqual(fillParts(bank.text), ['She ', ' her car on the ', '.'])
  let answer = fillWord(emptyAnswer(bank), 'puts')
  answer = fillWord(answer, 'paper')
  assert.deepEqual(answer.blanks, ['puts', 'paper'])
  assert.equal(fillWord(answer, 'folds'), answer)  // Full.
  answer = setBlank(answer, 0, '')
  assert.equal(ready(bank, answer), false)
  assert.deepEqual(fillWord(answer, 'folds').blanks, ['folds', 'paper'])
  const typed = setBlank(emptyAnswer(free), 0, '  ')
  assert.equal(ready(free, typed), false)
  assert.deepEqual(toResponse(setBlank(typed, 0, 'puts')), { blanks: ['puts'] })
})

test('shows the task input only while answering a clicked task', () => {
  const snapshot = { task: choice, wait_kind: 'answer', recording: null, mode: 'waiting_input' }
  assert.equal(answerMode(snapshot), 'task')
  assert.equal(answerMode({ ...snapshot, wait_kind: 'retry' }), 'task')
  assert.equal(answerMode({ ...snapshot, wait_kind: 'closing_confirmation' }), 'text')  // Confirming the close.
  assert.equal(answerMode({ ...snapshot, task: null, recording: { reference_text: 'x' } }), 'reading')
  assert.equal(answerMode({ ...snapshot, task: null }), 'text')
  assert.equal(answerMode({ ...snapshot, mode: 'ended' }), 'none')
  assert.equal(answerMode(undefined), 'none')
})

test('reading the story is answered with the reading buttons only', () => {
  for (const wait_kind of ['reading_start', 'reading'])
    for (const mode of ['waiting_input', 'delivering', 'paused'])
      assert.equal(answerMode({ mode, wait_kind, task: null, recording: null }), 'story')
})

test('picture options show only when every picture loads', () => {
  assert.equal(picturesSettled([true, undefined, true]), 'loading')
  assert.equal(picturesSettled([true, true, true]), 'pictures')
  assert.equal(picturesSettled([true, false, undefined]), 'words')
  assert.equal(picturesSettled([]), 'words')
})
