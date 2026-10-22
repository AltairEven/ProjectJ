import assert from 'node:assert/strict'
import test from 'node:test'
import { requestFailure } from '../src/request.ts'

test('only unknown outcomes offer to resume the same request', () => {
  assert.equal(requestFailure(undefined, 'Failed to fetch').unknown, true)
  assert.equal(requestFailure(503, 'The save or receipt did not come back.').unknown, true)
  for (const status of [400, 403, 404, 409, 422, 423]) assert.equal(requestFailure(status, 'stale input binding').unknown, false)
})

test('technical rejections are explained and keep the original detail', () => {
  const failure = requestFailure(409, 'stale input binding')
  assert.match(failure.message, /^This is not possible right now/)
  assert.equal(failure.detail, 'stale input binding')
  assert.match(requestFailure(undefined, 'Failed to fetch').message, /^The result did not come back/)
})

test('messages written for the student are shown as they are', () => {
  const text = 'Nothing to confirm now. Please answer the current question.'
  assert.equal(requestFailure(400, text).message, text)
  assert.equal(requestFailure(503, 'The lesson could not be made.').message, 'The lesson could not be made.')
})
