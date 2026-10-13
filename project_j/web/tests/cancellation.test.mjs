import assert from 'node:assert/strict'
import test from 'node:test'
import { acknowledgePause, cancel, presentationCancelled } from '../src/cancellation.ts'

const view = (core_revision, mode = 'delivering') => ({ core_revision, mode })

test('stop survives ordinary input, delivery, connection responses and missing acknowledgement', () => {
  const stopped = cancel(undefined, 'stop-1', 'stop')
  for (const snapshot of [undefined, view(10), view(11), view(20, 'waiting_input'), view(21, 'ended')]) {
    assert.equal(presentationCancelled(stopped, snapshot), true)
  }
  assert.equal(acknowledgePause(stopped, 'stop-1', view(12, 'paused')), stopped)
  assert.equal(cancel(stopped, 'pause-after-stop', 'pause'), stopped)
})

test('pause needs its own acknowledgement and a later running core revision', () => {
  const pending = cancel(undefined, 'pause-1', 'pause')
  assert.equal(presentationCancelled(pending, view(11)), true)
  assert.equal(acknowledgePause(pending, 'other-pause', view(12, 'paused')), pending)
  assert.equal(acknowledgePause(pending, 'pause-1', view(12)), pending)
  const paused = acknowledgePause(pending, 'pause-1', view(12, 'paused'))
  for (const snapshot of [view(11), view(12), view(12, 'paused'), view(13, 'ended')]) {
    assert.equal(presentationCancelled(paused, snapshot), true)
  }
  assert.equal(presentationCancelled(paused, view(13)), false)
  assert.equal(presentationCancelled(paused, view(13, 'offscreen')), false)
})

test('late pause acknowledgement cannot undo a later stop or acknowledge another pause', () => {
  const first = cancel(undefined, 'pause-1', 'pause')
  const second = cancel(first, 'pause-2', 'pause')
  assert.equal(acknowledgePause(second, 'pause-1', view(12, 'paused')), second)
  const stopped = cancel(second, 'stop-1', 'stop')
  assert.equal(presentationCancelled(acknowledgePause(stopped, 'pause-2', view(13, 'paused')), view(14)), true)
})

test('resume arriving before the pause HTTP acknowledgement is still ordered by core revision', () => {
  const pending = cancel(undefined, 'pause-1', 'pause')
  const resumed = view(15)
  assert.equal(presentationCancelled(pending, resumed), true)
  const acknowledged = acknowledgePause(pending, 'pause-1', view(12, 'paused'))
  assert.equal(presentationCancelled(acknowledged, resumed), false)
  assert.equal(presentationCancelled(undefined, view(1)), false)
})
