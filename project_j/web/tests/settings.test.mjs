import assert from 'node:assert/strict'
import test from 'node:test'
import { bindingRows, checkConfirm, checkLine, deadlineLabels, modelOptions, probeNotes, startNotice, withCurrent } from '../src/settings.ts'

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

test('binding rows list the six purposes in order, missing ones blank', () => {
  const rows = bindingRows(models)
  assert.deepEqual(rows.map(row => row.purpose), ['planning', 'preparation', 'classroom_judgment', 'evaluation', 'vocabulary', 'reading'])
  assert.deepEqual(rows[3], { purpose: 'evaluation', adapter: 'codex', model: 'gpt-6.1-sol', effort: 'xhigh', tier: 'default' })
  assert.deepEqual(rows[2].tier, 'fast')
  assert.deepEqual(rows[1], { purpose: 'preparation', adapter: '', model: '', effort: '', tier: '' })
})

test('probe notes say what a refused setup means', () => {
  assert.deepEqual(probeNotes(models), [])
  assert.deepEqual(probeNotes({ ...models, initialized: false }), [
    'This setup has not passed the probe; classes, preparation, evaluation and planning are refused until it does.',
  ])
  assert.deepEqual(probeNotes(undefined).length, 1)
  assert.equal(probeNotes.length, 1)  // one argument: the reference-set reminder moved to the check line
})

test('model choices are what the adapter offers now, keeping the saved model', () => {
  const row = { purpose: 'evaluation', adapter: 'codex', model: 'gpt-6.1-sol', effort: 'xhigh', tier: 'default' }
  assert.deepEqual(modelOptions({ codex: ['gpt-6.2-sol', 'gpt-6.1-sol'] }, row), ['gpt-6.2-sol', 'gpt-6.1-sol'])
  assert.deepEqual(modelOptions({ codex: ['gpt-6.2-sol'] }, row), ['gpt-6.1-sol', 'gpt-6.2-sol'])
  assert.deepEqual(modelOptions({ codex: ['gpt-6.2-sol'] }, { ...row, model: '' }), ['', 'gpt-6.2-sol'])
  assert.equal(modelOptions({ simulated: ['simulated-jane'] }, row), undefined)  // no list for this adapter: type it
  assert.equal(modelOptions({ codex: [] }, row), undefined)
})

test('a choice keeps the current value, blank included, so the select shows what will be saved', () => {
  assert.deepEqual(withCurrent(['low', 'medium'], 'medium'), ['low', 'medium'])
  assert.deepEqual(withCurrent(['low', 'medium'], ''), ['', 'low', 'medium'])
  assert.deepEqual(withCurrent(['low', 'medium'], 'max'), ['max', 'low', 'medium'])
})

const judged = (state, report = null) => ({
  state, report, cases: 92, estimate_seconds: 368, rerun_cases: state === 'needs_rerun' ? ['F1-correct', 'F2-variant'] : [],
  binding: { adapter: 'codex', model: 'gpt-6.2-sol', effort: 'low', tier: 'fast' },
})

test('the check line says where the current setup stands', () => {
  assert.equal(checkLine(judged('not_run')), 'Not run for this setup yet.')
  assert.equal(checkLine(judged('passed', { passed: 92, total: 92, settled_by_rerun: false })), 'Passed (92/92).')
  assert.equal(checkLine(judged('passed', { passed: 90, total: 92, settled_by_rerun: true })), 'Passed (90/92, settled by rerun).')
  assert.equal(checkLine(judged('failed')), 'Failed: some answers were judged wrong. See below.')
  assert.equal(checkLine(judged('needs_rerun')), 'Some calls failed; rerun those questions.')
})

test('the confirmation names the questions, the model and the minutes, rounded up', () => {
  assert.equal(checkConfirm(judged('not_run')),
    'This asks 92 questions to gpt-6.2-sol through your ChatGPT subscription and takes about 7 minutes. '
    + 'Classes cannot start while it runs; evaluations and planning wait until it finishes.')
  assert.match(checkConfirm({ ...judged('not_run'), estimate_seconds: 60 }), /about 1 minutes/)
})

test('the course page notes a setup that has not passed, without stopping the class', () => {
  const note = state => `Classroom judgment for the current model has not passed the reference set (${state}). You can still start; see Settings.`
  assert.equal(startNotice(judged('not_run')), note('not run'))
  assert.equal(startNotice(judged('failed')), note('failed'))
  assert.equal(startNotice(judged('needs_rerun')), note('needs a rerun'))
  assert.equal(startNotice(judged('passed', { passed: 92, total: 92 })), null)
  assert.equal(startNotice(null), null)
  assert.equal(startNotice(undefined), null)
})
