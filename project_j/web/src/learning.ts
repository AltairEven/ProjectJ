import type { Document as Doc, RevisionRef as Ref } from './client'
import { judgmentLabels } from './report.ts'

type Body = Record<string, any>
type Shape = Pick<Doc, 'object_id' | 'revision' | 'kind'> & { body: Body, created_at?: string, source_revision?: number }
export type EvaluationState = '评价中' | '等你认可' | '已认可' | '有异议'

const helpLabels: Record<string, string> = { none: '无帮助', light_prompt: '轻提示', scaffold: '支架', full_model: '完整示范', unknown: '帮助未知' }
const ref = (doc: Shape): Ref => ({ object_id: doc.object_id, revision: doc.revision })
const nameOf = (catalog: Shape[], id: string) => catalog.find(doc => doc.kind === 'capability' && doc.body.dimension_id === id)?.body.name as string | undefined

/** One line per dimension; without evidence the judgment is unknown, never a score (design/learning). */
export function dimensionLines(state: Shape, catalog: Shape[]) {
  return ((state.body.dimensions ?? []) as Body[]).map(item => {
    const evidence = (item.evidence_refs ?? []) as Ref[]
    const limits = ((item.limitations ?? []) as string[]).join('')  // whole sentences
    return {
      name: nameOf(catalog, item.dimension_id) ?? item.dimension_id,
      judgment: evidence.length ? judgmentLabels[item.judgment] ?? item.judgment : '未知',
      basis: evidence.length
        ? `依据${evidence.length}条课堂记录，${helpLabels[item.help] ?? item.help}，材料：${item.material_condition}。${limits}`
        : limits || '还没有依据。',
    }
  })
}

/** An evaluation is current when it read the fact and every note added to it (server rule). */
export function evaluationRows(objects: Shape[]) {
  return objects.filter(doc => doc.kind === 'fact')
    .sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
    .map(fact => {
      const activity = String(fact.body.activity_id)
      const notes = objects.filter(doc => doc.kind === 'feedback' && doc.body.activity_id === activity)
      const evaluation = objects.find(doc => doc.kind === 'evaluation' && doc.body.activity_id === activity)
      if (!evaluation || evaluation.source_revision !== fact.revision + notes.length) return { activity_id: activity, state: '评价中' as EvaluationState }
      const evaluation_ref = ref(evaluation)
      const acknowledged = objects.some(doc => doc.kind === 'report' && doc.body.activity_id === activity
        && doc.body.acknowledged_ref?.object_id === evaluation_ref.object_id && doc.body.acknowledged_ref?.revision === evaluation_ref.revision)
      const last = notes.sort((a, b) => (b.source_revision ?? 0) - (a.source_revision ?? 0))[0]
      const state: EvaluationState = acknowledged ? '已认可' : last?.body.corrections?.length ? '有异议' : '等你认可'
      return { activity_id: activity, state, evaluation_ref }
    })
}

export function goalCard(demand: Shape, catalog: Shape[]) {
  const proposed = (demand.body.proposed_capabilities ?? []) as Body[]
  const name = (id: string) => nameOf(catalog, id) ?? proposed.find(item => item.dimension_id === id)?.name ?? id
  const goal = (item: Body) => `${name(item.dimension_id)}：${item.description}`
  return {
    primary: goal(demand.body.primary_goal), auxiliary: goal(demand.body.auxiliary_goal),
    reason: (demand.body.goal_reason as string | undefined) ?? '无',
    newCapabilities: proposed.map(item => `${item.name}：${item.definition}`),
  }
}
