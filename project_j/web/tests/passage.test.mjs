import assert from 'node:assert/strict'
import test from 'node:test'
import { passage } from '../src/passage.ts'

test('a reading text shows its title and then numbered paragraphs, as Jane counts them', () => {
  const text = 'Checking a Trail Sensor\n\nMia volunteers with a group.\nShe likes it.\n\n\nIn June the numbers dropped.\n'
  assert.deepEqual(passage(text), { title: 'Checking a Trail Sensor',
    paragraphs: ['Mia volunteers with a group.\nShe likes it.', 'In June the numbers dropped.'] })
})

test('a text without a separate title keeps every block as a paragraph', () => {
  assert.deepEqual(passage('One long paragraph that is the whole text.\n'), { title: '', paragraphs: ['One long paragraph that is the whole text.'] })
  assert.deepEqual(passage(''), { title: '', paragraphs: [] })
})
