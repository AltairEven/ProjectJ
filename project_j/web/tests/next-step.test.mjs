import assert from 'node:assert/strict'
import test from 'node:test'
import { inOnboarding, jobLines, nextStepView, pages } from '../src/next-step.ts'

const kinds = ['lexile', 'chat', 'placement', 'profile', 'class', 'interrupted', 'decision', 'evaluation',
  'goal', 'course', 'resources', 'waiting', 'none']
const subjects = {
  evaluation: { activity_id: 'a1', evaluation_ref: { object_id: 'e', revision: 1 } },
  course: { course_ref: { object_id: 'c1', revision: 2 }, confirmed: false },
  resources: { name: 'start:c1:2', state: 'running', reason: null, progress: '2/5' },
}

test('the navigation lists the six pages in order', () => {
  assert.deepEqual(pages.map(([page]) => page), ['home', 'state', 'plan', 'courses', 'evaluations', 'settings'])
  assert.deepEqual(pages.map(([, label]) => label), ['主页', '学习状态', '学习计划', '课程', '课后评价', '设置'])
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
  assert.deepEqual([evaluation.action, evaluation.page, evaluation.detail], ['查看并认可', 'evaluations', 'a1'])
  const course = nextStepView({ kind: 'course', subject: subjects.course })
  assert.deepEqual([course.action, course.page, course.detail], ['查看并确认', 'courses', 'c1'])
  assert.equal(nextStepView({ kind: 'goal', subject: {} }).action, '确认新目标')
  assert.equal(nextStepView({ kind: 'waiting', subject: { failed: [] } }).action, undefined)
  assert.equal(nextStepView({ kind: 'none', subject: {} }).action, undefined)
  const resources = nextStepView({ kind: 'resources', subject: subjects.resources })
  assert.deepEqual([resources.page, resources.detail], ['courses', 'c1'])
  assert.match(resources.title, /第2\/5项/)
  const failed = nextStepView({ kind: 'resources', subject: { ...subjects.resources, state: 'failed', reason: '资源未就绪', progress: null } })
  assert.match(failed.title, /失败/)
  assert.match(failed.title, /资源未就绪/)
})

test('background jobs read as what the AI is doing, with the reason when one failed', () => {
  assert.deepEqual(jobLines([
    { name: 'prepare:d:1', state: 'running', reason: null, progress: null },
    { name: 'evaluate:a1:2', state: 'running' },
    { name: 'adopt:e:1', state: 'running' },
    { name: 'summarize:a1', state: 'running' },
    { name: 'review:week:3', state: 'running' },
    { name: 'start:c1:2', state: 'running', progress: '2/5' },
    { name: 'prepare:d:2', state: 'failed', reason: '备课未取得（超时）' },
  ]), [
    '正在备课', '正在评价', '正在更新学习状态与计划', '正在整理学习资料', '正在复盘',
    '正在准备资源并开课：第2/5项', '备课失败：备课未取得（超时）',
  ])
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
