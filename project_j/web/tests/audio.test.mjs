import assert from 'node:assert/strict'
import test from 'node:test'
import { decodePcm16, encodeWav, toBase64, toMono } from '../src/audio.ts'

test('a sample split across network chunks decodes once and in place', () => {
  const first = decodePcm16(new Uint8Array([0x00, 0x40, 0xff]), null)
  assert.deepEqual(Array.from(first.samples), [0.5]); assert.equal(first.carry, 0xff)
  const second = decodePcm16(new Uint8Array([0xbf]), first.carry)
  assert.deepEqual(Array.from(second.samples), [-16385 / 32768]); assert.equal(second.carry, null)
})

test('a 16 kHz recording becomes a mono PCM16 WAV the scorer reads', () => {
  const wav = encodeWav(new Float32Array([0, 1, -1, 2]), 16000)
  const view = new DataView(wav.buffer)
  assert.equal(new TextDecoder().decode(wav.slice(0, 4)), 'RIFF')
  assert.equal(new TextDecoder().decode(wav.slice(8, 16)), 'WAVEfmt ')
  assert.equal(view.getUint16(20, true), 1); assert.equal(view.getUint16(22, true), 1)
  assert.equal(view.getUint32(24, true), 16000); assert.equal(view.getUint32(28, true), 32000)
  assert.equal(view.getUint16(34, true), 16); assert.equal(view.getUint32(40, true), 8)
  assert.deepEqual([view.getInt16(44, true), view.getInt16(46, true), view.getInt16(48, true), view.getInt16(50, true)], [0, 32767, -32767, 32767])
})

test('stereo is averaged to mono', () => {
  assert.deepEqual(Array.from(toMono([new Float32Array([1, 0]), new Float32Array([0, 0])])), [0.5, 0])
})

test('a 300 second recording converts to base64 without overflowing the stack', () => {
  const bytes = new Uint8Array(300 * 16000 * 2 + 44).fill(7)
  assert.equal(Buffer.from(toBase64(bytes), 'base64').equals(Buffer.from(bytes)), true)
})
