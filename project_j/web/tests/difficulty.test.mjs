import assert from 'node:assert/strict'
import test from 'node:test'
import { storyLevelLine } from '../src/courses.ts'
import { canRate, challengeOption, ratingHint, ratingOf } from '../src/learning.ts'

const doc = (object_id, revision, kind, body) => ({ object_id, revision, kind, body })
const fact = first => doc('f', 1, 'fact', {
  activity_id: 'a1', course_ref: { object_id: 'c', revision: 1 },
  events: [{ kind: 'reading_finished', payload: { reading_ms: 60000, word_count: 300, first_reading: first, pauses: 0 } }],
})
const course = level => doc('c', 1, 'course', { story_level: level })
const ack = rating => doc('k', 1, 'report', { activity_id: 'a1', acknowledged_ref: { object_id: 'e', revision: 1 }, ...(rating ? { story_rating: rating } : {}) })
const level = (lexile, challenge, target) => ({ body: { story_level: { lexile, challenge, target, words: [250, 450] } } })

test('challenge options show the target or the step', () => {
  assert.equal(challengeOption('little', '600L'), 'A little harder (600L)')
  assert.equal(challengeOption('same'), 'Same as my level')
})

test('only a first reading of a leveled story can be rated, once', () => {
  assert.equal(canRate(fact(true), [course({ challenge: 'challenge' })]), true)
  assert.equal(canRate(fact(false), [course({ challenge: 'challenge' })]), false)
  assert.equal(canRate(fact(true), [course(null)]), false)
  assert.equal(canRate(fact(true), [course({ challenge: null })]), false)
  assert.equal(canRate(fact(true), [course({ challenge: 'challenge' }), ack()]), false)
})

test('the rating shows after acknowledging', () => {
  assert.equal(ratingOf('a1', [ack('just_right')]), 'Just right')
  assert.equal(ratingOf('a1', [ack()]), null)
})

test('a rating past the end explains what to do', () => {
  assert.match(ratingHint('harder', 'too_easy'), /^You are already at the hardest setting\./)
  assert.match(ratingHint('same', 'too_hard'), /^You are already at the easiest setting\./)
  assert.equal(ratingHint('challenge', 'too_easy'), null)
})

test('the course says what level the story aimed at', () => {
  assert.equal(storyLevelLine(level('550L', 'challenge', '650L')), 'Aimed at about 650L (your level 550L + 100L); not measured')
  assert.equal(storyLevelLine(level('550L', 'same', '550L')), 'Aimed at about 550L (your level 550L); not measured')
  assert.equal(storyLevelLine(level('550L', null, '550L')), 'Aimed at about 550L (your level 550L); not measured')
  assert.equal(storyLevelLine({ body: {} }), null)
})
