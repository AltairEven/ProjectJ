import assert from 'node:assert/strict'
import test from 'node:test'
import { chatComplete, lexileError } from '../src/onboarding.ts'

test('a Lexile measure is checked as the server checks it', () => {
  for (const ok of ['650L', ' 650 l ', 'BR100L', 'br 100l', '0L', '1600L']) assert.equal(lexileError(ok), null, ok)
  assert.match(lexileError(''), /请填写/)
  assert.match(lexileError('   '), /请填写/)
  assert.match(lexileError('650'), /L/)
  assert.match(lexileError('12345L'), /4位/)
  assert.match(lexileError('abc'), /650L/)
})

test('the chat is complete when every question has a non-blank answer', () => {
  assert.equal(chatComplete(['a', 'b', 'c', 'd'], 4), true)
  assert.equal(chatComplete(['a', 'b', 'c'], 4), false)
  assert.equal(chatComplete(['a', ' ', 'c', 'd'], 4), false)
  assert.equal(chatComplete([], 0), false)
})
