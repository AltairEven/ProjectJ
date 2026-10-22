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
