import assert from 'node:assert/strict'
import test from 'node:test'
import { chosenTopic, focusError, inOnboarding, initialFocus, jobLines, nextStepView, pages, retriedOnHome } from '../src/next-step.ts'

const kinds = ['lexile', 'chat', 'placement', 'profile', 'class', 'interrupted', 'decision', 'evaluation',
  'goal', 'focus', 'course', 'resources', 'waiting', 'none']
const subjects = {
  evaluation: { activity_id: 'a1', evaluation_ref: { object_id: 'e', revision: 1 } },
  course: { course_ref: { object_id: 'c1', revision: 2 }, confirmed: false },
  resources: { name: 'start:c1:2', state: 'running', reason: null, progress: '2/5' },
}

test('the navigation lists the seven pages in order', () => {
  assert.deepEqual(pages.map(([page]) => page), ['home', 'state', 'plan', 'courses', 'evaluations', 'vocabulary', 'settings'])
  assert.deepEqual(pages.map(([, label]) => label), ['Home', 'Progress', 'Plan', 'Lessons', 'Evaluations', 'Vocabulary', 'Settings'])
})

test('every next step has a title and a page to go to', () => {
  const targets = ['home', 'state', 'plan', 'courses', 'evaluations', 'settings', 'onboarding', 'class']
  for (const kind of kinds) {
    const view = nextStepView({ kind, subject: subjects[kind] ?? {} })
    assert.ok(view.title, kind)
    assert.ok(targets.includes(view.page), kind)
  }
  for (const kind of ['lexile', 'chat', 'placement', 'profile']) assert.equal(nextStepView({ kind, subject: {} }).page, 'onboarding')
  assert.equal(nextStepView({ kind: 'class', subject: {} }).page, 'class')
})

test('the student is asked to act with the right words, and waiting asks nothing', () => {
  const evaluation = nextStepView({ kind: 'evaluation', subject: subjects.evaluation })
  assert.deepEqual([evaluation.action, evaluation.page, evaluation.detail], ['Read and acknowledge', 'evaluations', 'a1'])
  const course = nextStepView({ kind: 'course', subject: subjects.course })
  assert.deepEqual([course.action, course.page, course.detail], ['Look and confirm', 'courses', 'c1'])
  // Confirmed is saved and waits: the student starts the class when ready.
  const ready = nextStepView({ kind: 'course', subject: { ...subjects.course, confirmed: true } })
  assert.deepEqual([ready.title, ready.action, ready.page, ready.detail], ['The lesson is confirmed. Start when you are ready.', 'Go to class', 'courses', 'c1'])
  assert.equal(nextStepView({ kind: 'goal', subject: {} }).action, 'Look at the goal')
  assert.equal(nextStepView({ kind: 'waiting', subject: { failed: [] } }).action, undefined)
  assert.equal(nextStepView({ kind: 'none', subject: {} }).action, undefined)
  const resources = nextStepView({ kind: 'resources', subject: subjects.resources })
  assert.deepEqual([resources.page, resources.detail], ['courses', 'c1'])
  assert.match(resources.title, /item 2\/5/)
  const failed = nextStepView({ kind: 'resources', subject: { ...subjects.resources, state: 'failed', reason: 'Resources not ready', progress: null } })
  assert.match(failed.title, /failed/)
  assert.match(failed.title, /Resources not ready/)
})

test('background jobs read as what the AI is doing, with the reason when one failed', () => {
  assert.deepEqual(jobLines([
    { name: 'prepare:d:1', state: 'running', reason: null, progress: null },
    { name: 'evaluate:a1:2', state: 'running' },
    { name: 'adopt:e:1', state: 'running' },
    { name: 'summarize:a1', state: 'running' },
    { name: 'review:week:3', state: 'running' },
    { name: 'start:c1:2', state: 'running', progress: '2/5' },
    { name: 'confirm:c1:2', state: 'running', progress: '1/3' },
    { name: 'recheck:e:2', state: 'running' },
    { name: 'prepare:d:2', state: 'failed', reason: 'The lesson could not be made (it took too long)' },
  ]), [
    'Making the lesson', 'Evaluating', 'Updating progress and plan', 'Writing your profile', 'Reviewing the plan',
    'Getting resources and starting: item 2/5', 'Confirming the lesson and getting resources: item 1/3',
    'Checking progress after your correction', 'Making the lesson failed: The lesson could not be made (it took too long)',
  ])
  assert.deepEqual(jobLines([{ name: 'reference', state: 'running', progress: '12/92' }]), ['Checking classroom judgment: item 12/92'])
})

test('a failed job is retried on the home page unless its own page starts it again', () => {
  const failed = name => ({ name, state: 'failed', reason: 'x' })
  assert.equal(retriedOnHome(failed('evaluate:a1:2')), true)
  assert.equal(retriedOnHome({ name: 'evaluate:a1:2', state: 'running' }), false)
  for (const name of ['start:c1:2', 'confirm:c1:2', 'reference']) assert.equal(retriedOnHome(failed(name)), false, name)
})

test('onboarding lasts until the profile is confirmed, also while the AI works on it', () => {
  const profile = confirmed => ({ kind: 'profile', body: { confirmed } })
  assert.equal(inOnboarding({ next: { kind: 'lexile' }, objects: [] }), true)
  assert.equal(inOnboarding({ next: { kind: 'waiting' }, objects: [profile(false)] }), true)
  assert.equal(inOnboarding({ next: { kind: 'waiting' }, objects: [profile(true)] }), false)
  assert.equal(inOnboarding({ next: { kind: 'goal' }, objects: [profile(true)] }), false)
  // In class the dashboard lists no objects; the classroom decides then.
  assert.equal(inOnboarding({ next: { kind: 'class' }, objects: [], active: {} }), false)
})

test('the skills to train are ticked on the main page, Jane\'s suggestions already ticked', () => {
  const focus = nextStepView({ kind: 'focus', subject: {} })
  assert.deepEqual([focus.title, focus.page, focus.action], ['What should the next lesson be about?', 'home', undefined])
  assert.deepEqual(initialFocus([{ skill_id: 'past_simple', reason: 'r' }]), ['past_simple'])
  assert.equal(focusError(3, '', 'Zelda'), null)
  assert.equal(focusError(0, '', 'Zelda'), null)
  assert.equal(focusError(1, '', '  '), 'Choose a topic, or write what you want to read about.')
  assert.equal(focusError(1, '', 'x'.repeat(101)), 'A topic can have at most 100 characters.')
  assert.equal(focusError(4, '', 'Zelda'), 'Choose at most 3 a lesson.')
  assert.equal(focusError(1, 'x'.repeat(101), 'Zelda'), 'Other things to practise: at most 100 characters.')
})

test('a stopped class can be acknowledged as it is or taken again', () => {
  const view = nextStepView({ kind: 'evaluation', subject: { ...subjects.evaluation, retake: { object_id: 'c1', revision: 2 } } })
  assert.deepEqual([view.title, view.action, view.page], ['The last class stopped early. Acknowledge its evaluation, or take the lesson again.', 'Read and acknowledge', 'evaluations'])
  assert.deepEqual(view.also, { action: 'Take it again', page: 'courses', detail: 'c1' })
  assert.equal(nextStepView({ kind: 'evaluation', subject: subjects.evaluation }).also, undefined)
})

test('an evaluation written again after the student\'s correction asks to be acknowledged again', () => {
  const view = nextStepView({ kind: 'evaluation', subject: { ...subjects.evaluation, corrected: true } })
  assert.deepEqual([view.title, view.action, view.detail], ['The evaluation was rewritten after your correction. Please acknowledge it again.', 'Read and acknowledge', 'a1'])
})

test('a chosen topic that is no longer listed is no topic, not a crash', () => {
  const topics = [{ title: 'Boats' }, { title: 'Bridges' }]
  assert.equal(chosenTopic(1, topics, ''), 'Bridges')
  assert.equal(chosenTopic(1, [], ''), '')  // Jane listed other topics while the page was open.
  assert.equal(chosenTopic('own', [], '  Trains '), 'Trains')
  assert.equal(chosenTopic(null, topics, ''), '')
})

test('remaking broken resources reads as such (ADR-0027)', () => {
  assert.deepEqual(jobLines([{ name: 'remake:c1:2', state: 'running', progress: '1/1' }]), ['Remaking broken resources: item 1/1'])
})
