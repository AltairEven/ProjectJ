import assert from 'node:assert/strict'
import test from 'node:test'
import { requestFailure } from '../src/request.ts'

test('only unknown outcomes offer to resume the same request', () => {
  assert.equal(requestFailure(undefined, 'Failed to fetch').unknown, true)
  assert.equal(requestFailure(503, '保存或回执未取得，请查询原操作，不要更换身份重建。').unknown, true)
  for (const status of [400, 403, 404, 409, 422, 423]) assert.equal(requestFailure(status, 'stale input binding').unknown, false)
})

test('raw English rejections are shown in Chinese and keep the original detail', () => {
  const failure = requestFailure(409, 'stale input binding')
  assert.match(failure.message, /[一-鿿]/)
  assert.equal(failure.detail, 'stale input binding')
  assert.equal(requestFailure(400, '当前不需要确认收到；请回答或补充当前问题。').message, '当前不需要确认收到；请回答或补充当前问题。')
})
