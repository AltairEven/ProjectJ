import type { Document as Doc, RevisionRef as Ref } from './client'
import { dateTimeText } from './dates.ts'

export type Body = Record<string, any>
/** A saved document as the pages read it; the server owns the shape of its body. */
export type Shape = Pick<Doc, 'object_id' | 'revision' | 'kind'> & { body: Body, created_at?: string, source_revision?: number }

/** A skill's name from the catalog documents, by its dimension id. */
export const capabilityName = (docs: Shape[], id: string) =>
  docs.find(doc => doc.kind === 'capability' && doc.body.dimension_id === id)?.body.name as string | undefined

const keyLabels: Record<string, string> = {
  report_ref: 'Report', evaluation_ref: 'Evaluation', course_ref: 'Lesson', preview_ref: 'Lesson details', check_ref: 'Content check', preparation: 'Lesson making',
  start: 'Start', unverified: 'Not verified', state_ref: 'Learning state', plan_ref: 'Plan', plan_effect: 'Plan change',
  demand_ref: 'Next lesson plan', suggestion_ref: 'Suggestion', review_ref: 'Review', decision_ref: 'Proposal', choice: 'Your choice',
  effect: 'Result', next_step: 'Next step', run_id: 'New run folder', result: 'Result', qualification: 'Use',
  status: 'Lesson status', reason: 'Reason', plan_reason: 'Why the plan changed',
}
const valueLabels: Record<string, string> = {
  drafted: 'Written, waiting for you', pending: 'Not started', actual_api: 'Real API', voice: 'Voice',
  reused: 'Kept', reused_with_reason: 'Kept, with a reason', plan_effective: 'The new plan is in use',
  not_changed: 'Not changed', recheck_demand_and_course: 'Check the plan and lesson again', accept: 'Accept', reject: 'Reject',
  restored_to_new_simulation: 'Restored to a new run folder', not_granted: 'Not granted', not_ready: 'Lesson not ready', adjusted: 'Changed',
  confirmed: 'Confirmed', awaiting_generation: 'Waiting to be made', planned: 'Listed; made after you confirm', generated: 'Ready to look at', adopted: 'In use', not_connected: 'Not connected',
}
const kindLabels: Record<string, string> = { image: 'Picture', audio: 'Voice', animation: 'Animation', video: 'Video' }
const stageLabels: Record<string, string> = { question: 'with the question', help: 'after help' }
const directionLabels: Record<string, string> = {
  consolidate: 'Practise', review: 'Review', extend: 'Go wider', advance: 'Small step up', observe: 'Short check',
}
export const judgmentLabels: Record<string, string> = {
  unknown: 'Unknown', needs_help: 'Needs help', single_independent: 'Done alone once',
  multiple_dates_independent: 'Done alone on several days', inconsistent: 'Mixed',
}
export const endLabels: Record<string, string> = {
  stop: 'Stopped', normal: 'Finished; summary confirmed', unconfirmed: 'Finished; summary not confirmed', interrupted: 'The program stopped; what happened is saved',
}

const isRef = (value: unknown): value is Ref =>
  typeof value === 'object' && value !== null && 'object_id' in value && 'revision' in value
const text = (value: unknown): string => Array.isArray(value) ? value.map(text).join(', ')
  : typeof value === 'string' ? valueLabels[value] ?? value : value == null ? 'None' : JSON.stringify(value)

function summary(doc: Doc, docs: Doc[]): string | undefined {
  const body = doc.body as Body
  const name = (id: string) => capabilityName(docs, id) ?? id
  switch (doc.kind) {
    case 'course': return body.parent_preview?.story
    case 'evaluation': return body.report?.summary
    case 'preview': return body.content?.story
    case 'suggestion': return body.direction
    case 'demand': return `${body.direction ? `${directionLabels[body.direction] ?? body.direction}: ` : ''}${body.primary_goal?.description}`
    case 'plan': return body.main_goal?.description
    case 'review': return body.conclusion
    case 'decision': return body.reason
    case 'state': return body.dimensions?.map((item: Body) => `${name(item.dimension_id)}: ${judgmentLabels[item.judgment] ?? item.judgment}`).join('; ')
  }
}

function reference(ref: Ref, docs: Doc[]): string {
  const doc = docs.find(item => item.object_id === ref.object_id)
  if (doc && doc.revision !== ref.revision) return `Version ${ref.revision} (now version ${doc.revision})`
  const about = doc && summary(doc, docs)
  return `Version ${ref.revision}${about ? `: ${about}` : ''}`
}

const joined = (items: unknown) => Array.isArray(items) && items.length ? items.join('; ') : 'None'

/** What a planned demand or a review adds beyond its one-line summary. */
function details(ref: Ref, docs: Doc[]): [string, string][] {
  const doc = docs.find(item => item.object_id === ref.object_id && item.revision === ref.revision)
  const body = doc?.body as Body | undefined
  if (doc?.kind === 'demand' && body?.note) {
    const note = body.note
    return [['Before', note.before], ['Now', note.after], ['Why', note.why], ['Not sure yet', joined(note.uncertain)], ['Next lesson', note.expectation]]
  }
  if (doc?.kind === 'review' && body) {
    return [['Still unknown', joined(body.unknown)], ...(body.checks?.length ? [['Rule changes', joined(body.checks)] as [string, string]] : [])]
  }
  return []
}

/** Flatten an operation result into labelled rows; references show what the saved object says. */
export function describe(value: unknown, docs: Doc[]): [string, string][] {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return [['Result', text(value)]]
  return Object.entries(value).flatMap(([key, item]): [string, string][] => {
    if (isRef(item)) return [[keyLabels[key] ?? key, reference(item, docs)], ...details(item, docs)]
    if (typeof item === 'object' && item !== null && !Array.isArray(item)) return describe(item, docs)
    return [[keyLabels[key] ?? key, text(item)]]
  })
}

const taskName = (task: Body, docs: Doc[]) => `${capabilityName(docs, task.dimension_id) ?? task.dimension_id} · ${task.task_id}`
const judgment = (task: Body) => judgmentLabels[task.working_judgment] ?? task.working_judgment

/** A model-drafted evaluation as rows: summary first, then each attempt and any rule changes. */
export function evaluationLines(doc: Doc, docs: Doc[]): [string, string][] {
  const body = doc.body as Body
  if (!body.report) return [['Evaluation', 'This version has no written evaluation.']]
  const rows: [string, string][] = [
    ['Summary', body.report.summary], ['Best evidence', joined(body.report.strongest)],
    ['Not sure yet', joined(body.report.uncertain)], ['Lesson vs class', body.report.alignment],
    ...(body.tasks ?? []).map((task: Body): [string, string] => [taskName(task, docs), `${judgment(task)}: ${task.explanation ?? ''}`]),
  ]
  // Out of 10 per question (ADR-0020); evaluations saved before scoring have none.
  rows.push(...(body.scores ?? []).map((score: Body): [string, string] => [`Score · ${taskName(score, docs)}`, scoreText(score)]))
  if (body.checks?.length) rows.push(['Rule changes', body.checks.map((check: Body) => `${check.task_id}: ${check.reason}`).join('; ')])
  return rows
}

/** "7/10 (Right after 1 wrong try, 1 hint used)" or "Unknown (Not finished when the class stopped)"; reading scores are uncalibrated. */
export function scoreText(score: Body): string {
  const known = typeof score.score === 'number'
  return `${known ? `${score.score}/10` : 'Unknown'} (${score.reason}${score.basis === 'pronunciation' ? '; the score limits are not calibrated' : ''})`
}

/** A rewritten evaluation against the version it replaced: the old summary, then each judgment that changed (design/interface). */
export function revisionChanges(current: Doc, previous: Doc, docs: Doc[]): [string, string][] {
  const before = new Map(((previous.body as Body).tasks ?? []).map((task: Body) => [task.task_id, task]))
  const changed = ((current.body as Body).tasks ?? []).flatMap((task: Body): [string, string][] => {
    const old = before.get(task.task_id) as Body | undefined
    return old && old.working_judgment !== task.working_judgment ? [[taskName(task, docs), `${judgment(old)} → ${judgment(task)}`]] : []
  })
  return [
    ['Earlier summary', (previous.body as Body).report?.summary ?? 'The earlier version has no written evaluation.'],
    ...(changed.length ? changed : [['Each question', 'No change'] as [string, string]]),
  ]
}

/** One line per teaching resource: kind, need, stage, purpose, Jane's words, fallback, state and versions. */
export function resourceLines(preview: Body): string[] {
  return (preview.resources ?? []).map((item: Body) => {
    const versions = item.versions?.length ? `${item.adopted_version ? `, version ${item.adopted_version} in use` : ''}, ${item.versions.length} in all` : ''
    return `${kindLabels[item.kind] ?? item.kind} (${item.required ? 'needed' : 'optional'}, ${stageLabels[item.stage] ?? item.stage}): ${item.purpose}`
      + `${item.text ? ` "${item.text}"` : ''}; without it: ${item.fallback} - ${valueLabels[item.state] ?? item.state}${versions}`
  })
}

/** How long the student read the story in class (ADR-0022), from the sealed class events. */
export function readingLine(events: { kind: string, payload: Record<string, unknown> }[]): string | undefined {
  const event = events.find(item => item.kind === 'reading_finished' || item.kind === 'reading_unfinished')
  if (!event) return undefined
  const ms = Number(event.payload.reading_ms), words = Number(event.payload.word_count)
  const seconds = Math.round(ms / 1000), minutes = Math.floor(seconds / 60)
  const time = minutes ? `${minutes} min ${seconds % 60} s` : `${seconds} s`
  const again = event.payload.first_reading ? '' : ' (read before)'
  if (event.kind === 'reading_unfinished') return `Not finished (read for ${time})${again}`
  const speed = ms > 0 ? `, about ${Math.round(words * 60000 / ms)} words a minute` : ''
  return `${time}, ${words} words${speed}${again}`
}

/** Each discussion round as the student's feedback and the model's note, then the confirmation. */
export function discussionLines(preview: Body): [string, string][] {
  const rounds = (preview.discussion ?? []).flatMap((item: Body, index: number): [string, string][] => [
    [`Round ${index + 1}: your note`, item.feedback], [`Round ${index + 1}: what changed`, item.note],
  ])
  const confirmation = preview.confirmation
  return [...rounds, ['Confirmed', confirmation ? `Yes (${dateTimeText(confirmation.confirmed_at)})` : 'Not yet']]
}

const purposeLabels: Record<string, string> = {
  planning: 'Planning', preparation: 'Lesson making', classroom_judgment: 'Class judgment', evaluation: 'Evaluation', vocabulary: 'Word meanings',
}
const itemLabels: Record<string, string> = {
  auth: 'Sign-in', model: 'Model', structured_output: 'Structured output', cancellation: 'Cancel', image_input: 'Image input', audio: 'Audio',
  tools: 'Tools off', 'tier:fast': 'Fast tier',
}
const statusLabels: Record<string, string> = {
  verified: 'verified', unverified: 'unverified', unsupported: 'not supported', failed: 'failed', not_probed: 'not probed',
}
const itemLabel = (name: string) => itemLabels[name] ?? (name.startsWith('effort:') ? `Effort ${name.slice(7)}` : name)

export function modelLines(models?: Body): string[] {
  if (!models?.purposes) return ['The model status is not available.']
  const head = models.initialized
    ? `The model is set up${models.simulated ? ' (simulated)' : ''}.`
    : 'The model is not set up; lessons, classes, reviews and evaluations cannot run yet.'
  const purposes = Object.entries(models.purposes as Record<string, Body>).map(([purpose, item]) => {
    const tier = item.binding?.tier === 'fast' ? ' · Fast' : ''
    const binding = item.binding ? `${item.binding.adapter} · ${item.binding.model} · ${item.binding.effort}${tier}` : 'Not set'
    const reasons = (item.reasons ?? []) as string[]
    return `${purposeLabels[purpose] ?? purpose}: ${binding}${reasons.length ? `; not passed: ${reasons.join('; ')}` : ''}`
  })
  // One line per capability report, items grouped by status in probe order; never credentials.
  const reports = ((models.reports ?? []) as Body[]).map(report => {
    const groups = new Map<string, string[]>()
    for (const [name, item] of Object.entries((report.items ?? {}) as Record<string, Body>)) {
      const status = statusLabels[item.status] ?? String(item.status)
      groups.set(status, [...(groups.get(status) ?? []), itemLabel(name)])
    }
    return `Report ${report.adapter} · ${report.model}: ${[...groups].map(([status, names]) => `${status} ${names.join(', ')}`).join('; ')}`
  })
  return [head, ...purposes, ...reports]
}
