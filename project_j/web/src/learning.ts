import type { Document as Doc, RevisionRef as Ref } from './client'
import { judgmentLabels } from './report.ts'

type Body = Record<string, any>
type Shape = Pick<Doc, 'object_id' | 'revision' | 'kind'> & { body: Body, created_at?: string, source_revision?: number }
export type EvaluationState = 'Evaluating' | 'Waiting for you' | 'Acknowledged' | 'Disputed'

const helpLabels: Record<string, string> = { none: 'no help', light_prompt: 'a small hint', scaffold: 'a frame', full_model: 'a full model', unknown: 'help unknown' }
const ref = (doc: Shape): Ref => ({ object_id: doc.object_id, revision: doc.revision })

/** For sort(): the newest saved document first; one without a time last. */
export const byNewest = (a: { created_at?: string }, b: { created_at?: string }) => (b.created_at ?? '').localeCompare(a.created_at ?? '')
const nameOf = (catalog: Shape[], id: string) => catalog.find(doc => doc.kind === 'capability' && doc.body.dimension_id === id)?.body.name as string | undefined

/** One line per dimension; without evidence the judgment is unknown, never a score (design/learning). */
export function dimensionLines(state: Shape, catalog: Shape[]) {
  return ((state.body.dimensions ?? []) as Body[]).map(item => {
    const evidence = (item.evidence_refs ?? []) as Ref[]
    const limits = ((item.limitations ?? []) as string[]).join('')  // whole sentences
    return {
      name: nameOf(catalog, item.dimension_id) ?? item.dimension_id,
      judgment: evidence.length ? judgmentLabels[item.judgment] ?? item.judgment : 'Unknown',
      basis: evidence.length
        ? `From ${evidence.length} class record${evidence.length === 1 ? '' : 's'}, ${helpLabels[item.help] ?? item.help}, material: ${item.material_condition}. ${limits}`
        : limits || 'No evidence yet.',
    }
  })
}

/** An evaluation is current when it read the fact and every note added to it (server rule). */
export function evaluationRows(objects: Shape[]) {
  return objects.filter(doc => doc.kind === 'fact')
    .sort(byNewest)
    .map(fact => {
      const activity = String(fact.body.activity_id)
      const notes = objects.filter(doc => doc.kind === 'feedback' && doc.body.activity_id === activity)
      const evaluation = objects.find(doc => doc.kind === 'evaluation' && doc.body.activity_id === activity)
      if (!evaluation || evaluation.source_revision !== fact.revision + notes.length) return { activity_id: activity, state: 'Evaluating' as EvaluationState }
      const evaluation_ref = ref(evaluation)
      const acknowledged = objects.some(doc => doc.kind === 'report' && doc.body.activity_id === activity
        && doc.body.acknowledged_ref?.object_id === evaluation_ref.object_id && doc.body.acknowledged_ref?.revision === evaluation_ref.revision)
      const last = notes.sort((a, b) => (b.source_revision ?? 0) - (a.source_revision ?? 0))[0]
      const state: EvaluationState = acknowledged ? 'Acknowledged' : last?.body.corrections?.length ? 'Disputed' : 'Waiting for you'
      return { activity_id: activity, state, evaluation_ref }
    })
}

export function goalCard(demand: Shape, catalog: Shape[]) {
  const proposed = (demand.body.proposed_capabilities ?? []) as Body[]
  const name = (id: string) => nameOf(catalog, id) ?? proposed.find(item => item.dimension_id === id)?.name ?? id
  const goal = (item: Body) => `${name(item.dimension_id)}: ${item.description}`
  return {
    primary: goal(demand.body.primary_goal), auxiliary: goal(demand.body.auxiliary_goal),
    reason: (demand.body.goal_reason as string | undefined) ?? 'None',
    newCapabilities: proposed.map(item => `${item.name}: ${item.definition}`),
  }
}

/** The story steps above the student's Lexile (ADR-0023): name, label, Lexile added. */
export const CHALLENGES: [string, string, number][] = [
  ['same', 'Same as my level', 0], ['little', 'A little harder', 50], ['challenge', 'Challenge', 100], ['harder', 'Harder', 150],
]
export const RATINGS: [string, string][] = [['too_hard', 'Too hard'], ['just_right', 'Just right'], ['too_easy', 'Too easy']]

export function challengeOption(name: string, extra?: string) {
  const label = CHALLENGES.find(([key]) => key === name)?.[1] ?? name
  return extra ? `${label} (${extra})` : label
}

/** Only a first reading of a story written at a step, before any acknowledgement (server rule). */
export function canRate(fact: Shape, objects: Shape[]) {
  const activity = fact.body.activity_id
  const course = objects.find(doc => doc.kind === 'course' && doc.object_id === fact.body.course_ref?.object_id && doc.revision === fact.body.course_ref?.revision)
  const first = ((fact.body.events ?? []) as Body[]).some(event => ['reading_finished', 'reading_unfinished'].includes(event.kind) && event.payload?.first_reading)
  const acknowledged = objects.some(doc => doc.kind === 'report' && doc.body.activity_id === activity && doc.body.acknowledged_ref)
  return Boolean(course?.body.story_level?.challenge) && first && !acknowledged
}

export function ratingOf(activity: string, objects: Shape[]) {
  const rating = objects.find(doc => doc.kind === 'report' && doc.body.activity_id === activity && doc.body.story_rating)?.body.story_rating
  return RATINGS.find(([key]) => key === rating)?.[1] ?? null
}

export function ratingHint(challenge: string, rating: string) {
  const where = 'update your Lexile measure in Your profile on the Progress page.'
  if (challenge === 'harder' && rating === 'too_easy') return `You are already at the hardest setting. If stories feel easy, ${where}`
  if (challenge === 'same' && rating === 'too_hard') return `You are already at the easiest setting. If stories feel hard, ${where}`
  return null
}
