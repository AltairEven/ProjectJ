import assert from 'node:assert/strict'
import test from 'node:test'
import { canConfirm, confirmationKey } from '../src/confirmation.ts'

test('only delivered goal and closing instructions offer confirmation', () => {
  for (const wait_kind of ['goal_confirmation', 'closing_confirmation']) {
    assert.equal(canConfirm({ mode: 'waiting_input', wait_kind }), true)
    for (const mode of ['delivering', 'judging', 'paused', 'offscreen', 'ended']) {
      assert.equal(canConfirm({ mode, wait_kind }), false)
    }
  }
})

test('answer, retry, clarification and reports never offer receipt confirmation', () => {
  assert.equal(canConfirm(), false)
  for (const wait_kind of ['answer', 'retry', 'clarification', 'offscreen_report', 'delivery_recovery']) {
    assert.equal(canConfirm({ mode: 'waiting_input', wait_kind }), false)
  }
})

test('the confirmation pop-up asks once for each thing Jane said while confirmation is open', () => {
  const waiting = { mode: 'waiting_input', wait_kind: 'goal_confirmation', wait_id: 'wait-1' }
  assert.equal(confirmationKey({ ...waiting, presented_action: { action_id: 'goal' } }), 'goal')
  assert.equal(confirmationKey({ ...waiting, presented_action: { action_id: 'clarified' } }), 'clarified')
  assert.equal(confirmationKey({ ...waiting, presented_action: null }), 'wait-1')
  assert.equal(confirmationKey({ ...waiting, wait_kind: 'answer', presented_action: { action_id: 'goal' } }), '')
  assert.equal(confirmationKey(), '')
})
