import assert from 'node:assert/strict'
import test from 'node:test'
import { bindingRows, deadlineLabels, probeNotes } from '../src/settings.ts'

const binding = (effort, tier = 'default') => ({ adapter: 'codex', model: 'gpt-6.1-sol', effort, tier })
const models = {
  initialized: true,
  purposes: {
    evaluation: { binding: binding('xhigh'), reasons: [] },
    classroom_judgment: { binding: binding('low', 'fast'), reasons: [] },
    planning: { binding: null, reasons: ['no model set for this use'] },
  },
}

test('the seven waiting-time keys have labels', () => {
  assert.deepEqual(Object.keys(deadlineLabels), [
    'classroom_judgment', 'vocabulary', 'evaluation', 'planning', 'preparation_drafting', 'preparation_image', 'preparation_animation',
    'preparation_image_check',
  ])
  assert.equal(deadlineLabels.preparation_image_check, 'Preparation: picture checks')
  assert.equal(deadlineLabels.preparation_image, 'Preparation: pictures')
})

test('binding rows list the five purposes in order, missing ones blank', () => {
  const rows = bindingRows(models)
  assert.deepEqual(rows.map(row => row.purpose), ['planning', 'preparation', 'classroom_judgment', 'evaluation', 'vocabulary'])
  assert.deepEqual(rows[3], { purpose: 'evaluation', adapter: 'codex', model: 'gpt-6.1-sol', effort: 'xhigh', tier: 'default' })
  assert.deepEqual(rows[2].tier, 'fast')
  assert.deepEqual(rows[1], { purpose: 'preparation', adapter: '', model: '', effort: '', tier: '' })
})

test('probe notes say what a refused setup means and when to rerun the reference set', () => {
  assert.deepEqual(probeNotes(models, false), [])
  assert.deepEqual(probeNotes({ ...models, initialized: false }, false), [
    'This setup has not passed the probe; classes, preparation, evaluation and planning are refused until it does.',
  ])
  assert.deepEqual(probeNotes(models, true), [
    'Classroom judgment changed: rerun the reference question set before relying on it.',
  ])
  assert.equal(probeNotes({ ...models, initialized: false }, true).length, 2)
})
