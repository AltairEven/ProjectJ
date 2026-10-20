// The main page's one next step and background lines, from what the server computed (ADR-0018).

export type Page = 'home' | 'state' | 'plan' | 'courses' | 'evaluations' | 'settings'
export type Target = Page | 'onboarding' | 'class'
type Job = { name: string, state: string, reason?: string | null, progress?: string | null }
type Subject = Record<string, any>

export const pages: [Page, string][] = [
  ['home', '主页'], ['state', '学习状态'], ['plan', '学习计划'], ['courses', '课程'], ['evaluations', '课后评价'], ['settings', '设置'],
]

const jobNames: Record<string, string> = {
  prepare: '备课', evaluate: '评价', adopt: '更新学习状态与计划', summarize: '整理学习资料', review: '复盘', start: '准备资源并开课',
}

function jobLine({ name, state, reason, progress }: Job): string {
  const what = jobNames[name.split(':')[0]] ?? name
  if (state === 'failed') return `${what}失败：${reason ?? '原因未知'}`
  return `正在${what}${progress ? `：第${progress}项` : ''}`
}

export const jobLines = (jobs: Job[]): string[] => jobs.map(jobLine)

const views: Record<string, (subject: Subject) => { title: string, action?: string, page: Target, detail?: string }> = {
  lexile: () => ({ title: '先填写蓝思值，开始建档', action: '开始建档', page: 'onboarding' }),
  chat: () => ({ title: '和 Jane 聊几句', action: '去聊天', page: 'onboarding' }),
  placement: () => ({ title: '建档小测已备好', action: '开始小测', page: 'onboarding' }),
  profile: () => ({ title: '学习资料已整理好，请确认', action: '查看并确认', page: 'onboarding' }),
  class: () => ({ title: '课堂进行中', action: '回到课堂', page: 'class' }),
  interrupted: () => ({ title: '有一节课中断了，需要处理', action: '去处理', page: 'settings' }),
  decision: () => ({ title: '有一个计划方案等你决定', action: '去决定', page: 'plan' }),
  evaluation: subject => ({ title: '课后评价已生成', action: '查看并认可', page: 'evaluations', detail: subject.activity_id }),
  goal: () => ({ title: '新目标等你确认', action: '确认新目标', page: 'plan' }),
  course: subject => ({ title: '下一课已备好', action: '查看并确认', page: 'courses', detail: subject.course_ref?.object_id }),
  resources: subject => ({ title: jobLine(subject as Job), action: subject.state === 'failed' ? '回到课程' : undefined, page: 'courses', detail: String(subject.name ?? '').split(':')[1] }),
  waiting: subject => ({ title: subject.failed?.length ? '有AI任务没有完成，见下方原因' : 'AI 正在处理，稍等即可', page: 'home' }),
  none: () => ({ title: '目前没有要做的事', page: 'home' }),
}

export function nextStepView(next: { kind: string, subject?: Subject }) {
  return (views[next.kind] ?? views.none)(next.subject ?? {})
}

const onboardingKinds = ['lexile', 'chat', 'placement', 'profile']

/** Until the profile is confirmed the student only onboards; the navigation waits (design/interface). */
export function inOnboarding(dashboard: { next?: { kind: string } | null, objects?: { kind: string, body: Subject }[], active?: unknown }): boolean {
  if (onboardingKinds.includes(dashboard.next?.kind ?? '')) return true
  if (dashboard.active) return false
  return !(dashboard.objects ?? []).some(doc => doc.kind === 'profile' && doc.body.confirmed)
}
