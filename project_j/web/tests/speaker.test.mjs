import assert from 'node:assert/strict'
import test from 'node:test'
import { reportsFor, startedReport } from '../src/speaker.ts'

const action = { action_id: 'a1', activity_id: 'act', task_instance_id: 't1', text: '你好' }
const now = new Date('2026-10-18T00:00:00Z')
const shape = reports => reports.map(r => [r.delivery_id, r.modality, r.status, r.sequence, r.delivered_range])

test('the first audible chunk reports audio started', () => {
  const report = startedReport(action, 'd1', now)
  assert.deepEqual(shape([report]), [['d1', 'audio', 'started', 1, ['audio']]])
  assert.equal(report.output_device_id, 'browser-speaker'); assert.equal(report.occurred_at, now.toISOString())
})

test('a finished sentence reports audio completed only', () => {
  assert.deepEqual(shape(reportsFor(action, { kind: 'completed', started: true }, false, 'd1', now)),
    [['d1', 'audio', 'completed', 2, ['audio']]])
})

test('a failure before any audio shows the text first, then records the failure', () => {
  assert.deepEqual(shape(reportsFor(action, { kind: 'failed', started: false }, false, 'd1', now)),
    [['d1:text', 'text', 'completed', 1, ['text_content']], ['d1', 'audio', 'failed', 1, []]])
})

test('a child cutting in mid-sentence gets the full text before the interruption is recorded', () => {
  assert.deepEqual(shape(reportsFor(action, { kind: 'interrupted', started: true }, false, 'd1', now)),
    [['d1:text', 'text', 'completed', 1, ['text_content']], ['d1', 'audio', 'interrupted', 2, ['audio_partial']]])
})

test('pause or stop records only the audio and shows no text', () => {
  assert.deepEqual(shape(reportsFor(action, { kind: 'interrupted', started: true }, true, 'd1', now)),
    [['d1', 'audio', 'interrupted', 2, ['audio_partial']]])
  assert.deepEqual(shape(reportsFor(action, { kind: 'interrupted', started: false }, true, 'd1', now)),
    [['d1', 'audio', 'cancelled', 1, []]])
})

test('every report has its own event identity', () => {
  const reports = [startedReport(action, 'd1', now), ...reportsFor(action, { kind: 'interrupted', started: true }, false, 'd1', now)]
  assert.equal(new Set(reports.map(r => r.delivery_event_id)).size, reports.length)
})
