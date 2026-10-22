import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { dateText, dateTimeText } from '../src/dates.ts'

const CHINESE = /[　-〿㐀-䶿一-鿿＀-￯]/
const root = fileURLToPath(new URL('..', import.meta.url))

function files(dir) {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === 'client' ? [] : files(path)
    return /\.(ts|tsx|css|html|json)$/.test(name) ? [path] : []
  })
}

test('the page and the sandbox show no Chinese', () => {
  const found = [join(root, 'src'), join(root, 'sandbox')].flatMap(files).flatMap(path =>
    readFileSync(path, 'utf8').split('\n').flatMap((line, i) =>
      CHINESE.test(line) ? [`${path.slice(root.length)}:${i + 1}`] : []))
  assert.deepEqual(found, [])
})

test('dates read in English', () => {
  assert.equal(dateText('2026-10-22T07:05:00Z'), 'Oct 22, 2026')
  assert.equal(dateTimeText(new Date(2026, 9, 22, 15, 5)), 'Oct 22, 2026, 3:05 PM')
})
