// The main page's one next step and background lines, from what the server computed (ADR-0018).

export type Page = 'home' | 'state' | 'plan' | 'courses' | 'evaluations' | 'vocabulary' | 'settings'
export type Target = Page | 'onboarding' | 'class'
type Job = { name: string, state: string, reason?: string | null, progress?: string | null }
type Subject = Record<string, any>

export const pages: [Page, string][] = [
  ['home', 'Home'], ['state', 'Progress'], ['plan', 'Plan'], ['courses', 'Lessons'], ['evaluations', 'Evaluations'], ['vocabulary', 'Vocabulary'], ['settings', 'Settings'],
]

const jobNames: Record<string, string> = {
  prepare: 'Making the lesson', evaluate: 'Evaluating', adopt: 'Updating progress and plan', summarize: 'Writing your profile', review: 'Reviewing the plan',
  start: 'Getting resources and starting', confirm: 'Confirming the lesson and getting resources', remake: 'Remaking broken resources', recheck: 'Checking progress after your correction', vocabulary: 'Looking up a word',
  reference: 'Checking classroom judgment',
}

function jobLine({ name, state, reason, progress }: Job): string {
  const what = jobNames[name.split(':')[0]] ?? name
  if (state === 'failed') return `${what} failed: ${reason ?? 'reason unknown'}`
  return `${what}${progress ? `: item ${progress}` : ''}`
}

export const jobLines = (jobs: Job[]): string[] => jobs.map(jobLine)

/** A failed confirm, start or reference check is started again from its own page, not the home page. */
export const retriedOnHome = ({ name, state }: Job): boolean =>
  state === 'failed' && !['start:', 'confirm:'].some(prefix => name.startsWith(prefix)) && name !== 'reference'

type Go = { action: string, page: Target, detail?: string }
const views: Record<string, (subject: Subject) => { title: string, action?: string, page: Target, detail?: string, also?: Go }> = {
  lexile: () => ({ title: 'Enter your Lexile to get started', action: 'Get started', page: 'onboarding' }),
  chat: () => ({ title: 'Chat with Jane', action: 'Chat', page: 'onboarding' }),
  placement: () => ({ title: 'Your first short lesson is ready', action: 'Start it', page: 'onboarding' }),
  profile: () => ({ title: 'Your profile is ready. Please check it.', action: 'Check it', page: 'onboarding' }),
  class: () => ({ title: 'A class is on', action: 'Back to class', page: 'class' }),
  interrupted: () => ({ title: 'A class stopped suddenly and needs a look', action: 'Look at it', page: 'settings' }),
  decision: () => ({ title: 'A plan change is waiting for you', action: 'Decide', page: 'plan' }),
  evaluation: subject => subject.retake
    ? { title: 'The last class stopped early. Acknowledge its evaluation, or take the lesson again.', action: 'Read and acknowledge', page: 'evaluations', detail: subject.activity_id,
      also: { action: 'Take it again', page: 'courses', detail: subject.retake.object_id } }
    : { title: subject.corrected ? 'The evaluation was rewritten after your correction. Please acknowledge it again.' : 'Your evaluation is ready', action: 'Read and acknowledge', page: 'evaluations', detail: subject.activity_id },
  goal: () => ({ title: 'A new goal is waiting for you', action: 'Look at the goal', page: 'plan' }),
  focus: () => ({ title: 'What should the next lesson be about?', page: 'home' }),  // Chosen right on the main page.
  course: subject => subject.confirmed
    ? { title: 'The lesson is confirmed. Start when you are ready.', action: 'Go to class', page: 'courses', detail: subject.course_ref?.object_id }
    : { title: 'The next lesson is ready', action: 'Look and confirm', page: 'courses', detail: subject.course_ref?.object_id },
  resources: subject => ({ title: jobLine(subject as Job), action: subject.state === 'failed' ? 'Back to the lesson' : undefined, page: 'courses', detail: String(subject.name ?? '').split(':')[1] }),
  waiting: subject => ({ title: subject.failed?.length ? 'Some work did not finish; see why below' : 'Jane is working on it. Please wait.', page: 'home' }),
  none: () => ({ title: 'Nothing to do right now', page: 'home' }),
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

/** Jane's suggested skills start ticked; the student may untick them. */
export const initialFocus = (suggestions: { skill_id: string }[]): string[] => suggestions.map(item => item.skill_id)

/** Mirrors the server: a topic of 1–100 characters, at most three skills and a short note. */
/** The chosen topic by its place in the list; the list may change while the page is open. */
export function chosenTopic(choice: number | 'own' | null, topics: { title: string }[], own: string): string {
  if (choice === 'own') return own.trim()
  return choice === null ? '' : topics[choice]?.title ?? ''
}

export function focusError(count: number, note: string, topic: string): string | null {
  if (!topic.trim()) return 'Choose a topic, or write what you want to read about.'
  if (topic.trim().length > 100) return 'A topic can have at most 100 characters.'
  if (count > 3) return 'Choose at most 3 a lesson.'
  if (note.trim().length > 100) return 'Other things to practise: at most 100 characters.'
  return null
}
