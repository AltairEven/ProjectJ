import assert from 'node:assert/strict'
import test from 'node:test'
import { finishRecording, idle, pauseRecording, recordedMs, resumeRecording, startRecording } from '../src/recording.ts'

test('pause and resume keep one recording and pauses do not count as reading time', () => {
  let r = startRecording(idle, 1000)
  r = pauseRecording(r, 3000)
  r = resumeRecording(r, 10000)
  assert.equal(recordedMs(r, 12000), 4000)
  const done = finishRecording(r, 12000, 300)
  assert.deepEqual(done, { input_started_ms: 1000, input_ended_ms: 12000, recording_ms: 4000,
    pause_count: 1, completion_trigger: 'explicit' })
})

test('reaching the maximum ends by time', () => {
  const r = startRecording(idle, 0)
  assert.equal(finishRecording(r, 300000, 300).completion_trigger, 'max_duration')
})

test('nothing to finish before starting, and transitions ignore the wrong state', () => {
  assert.equal(finishRecording(idle, 5, 300), undefined)
  assert.equal(pauseRecording(idle, 5), idle)
  const r = startRecording(idle, 0)
  assert.equal(startRecording(r, 9), r)
  assert.equal(resumeRecording(r, 9), r)
})
