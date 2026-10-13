import type { Document as Doc, RevisionRef as Ref } from './client'

type Body = Record<string, any>

const keyLabels: Record<string, string> = {
  report_ref: '汇总报告', course_ref: '课程', preview_ref: '家长预览', check_ref: '内容核对', preparation: '备课',
  start: '开始', unverified: '未验证', state_ref: '学习状态', plan_ref: '计划', plan_effect: '计划处理',
  demand_ref: '下一课需求', suggestion_ref: '下节建议', review_ref: '复盘', decision_ref: '具体方案', choice: '家长选择',
  effect: '结果', next_step: '下一步', run_id: '新模拟目录', result: '结果', qualification: '使用资格',
}
const valueLabels: Record<string, string> = {
  fixed_simulation_checked: '固定模拟包已核对', pending: '待开始', actual_api: '实际API', voice: '声音',
  child_entry: '儿童入口', reused: '沿用', reused_with_reason: '沿用并说明理由', plan_effective: '新计划已生效',
  not_changed: '未改变', recheck_demand_and_course: '复核需求和课程', accept: '接受', reject: '拒绝',
  restored_to_new_simulation: '已恢复到新模拟目录', not_granted: '未授予',
}
const judgmentLabels: Record<string, string> = {
  unknown: '未知', needs_help: '需要帮助', single_independent: '单次独立',
  multiple_dates_independent: '多日独立', inconsistent: '表现不一致',
}
export const endLabels: Record<string, string> = {
  stop: '明确停止', normal: '正常结束并确认小结', unconfirmed: '已结束，小结未确认', interrupted: '服务中断，已封存已发生范围',
}

const isRef = (value: unknown): value is Ref =>
  typeof value === 'object' && value !== null && 'object_id' in value && 'revision' in value
const text = (value: unknown): string => Array.isArray(value) ? value.map(text).join('、')
  : typeof value === 'string' ? valueLabels[value] ?? value : value == null ? '无' : JSON.stringify(value)

function summary(doc: Doc, docs: Doc[]): string | undefined {
  const body = doc.body as Body
  const name = (id: string) => (docs.find(item => item.kind === 'capability' && item.body.dimension_id === id)?.body.name as string) ?? id
  switch (doc.kind) {
    case 'course': return body.parent_preview?.story
    case 'preview': return body.content?.story
    case 'suggestion': return body.direction
    case 'demand': return body.primary_goal?.description
    case 'plan': return body.main_goal?.description
    case 'review': return body.conclusion
    case 'decision': return body.reason
    case 'state': return body.dimensions?.map((item: Body) => `${name(item.dimension_id)}：${judgmentLabels[item.judgment] ?? item.judgment}`).join('；')
  }
}

function reference(ref: Ref, docs: Doc[]): string {
  const doc = docs.find(item => item.object_id === ref.object_id)
  if (doc && doc.revision !== ref.revision) return `修订${ref.revision}（当前已是修订${doc.revision}）`
  const about = doc && summary(doc, docs)
  return `修订${ref.revision}${about ? `：${about}` : ''}`
}

/** Flatten an operation result into labelled rows; references show what the saved object says. */
export function describe(value: unknown, docs: Doc[]): [string, string][] {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return [['结果', text(value)]]
  return Object.entries(value).flatMap(([key, item]): [string, string][] => {
    if (isRef(item)) return [[keyLabels[key] ?? key, reference(item, docs)]]
    if (typeof item === 'object' && item !== null && !Array.isArray(item)) return describe(item, docs)
    return [[keyLabels[key] ?? key, text(item)]]
  })
}

const startItems: Record<string, string> = {
  materials: '材料', time: '时间', accompaniment: '陪伴', explicit_start: '明确开始', raw_report: '条件报告',
  'build-bound simulation verification': '与当前构建匹配的模拟检查依据',
}
const startReasons: Record<string, string> = {
  'required household condition failed': '必要条件报告为没有（材料、时间或陪伴），本次不开课；条件具备后再开始。',
  'content check unavailable or affected restriction active': '课程内容核对缺失或受影响的限制尚未解除，本包不能开始；请先复核或重新准备。',
  'memory core is absent; do not reopen from the old request': '原开始请求对应的课堂已不在内存中，不能按旧请求重开；请重新开始。',
}

/** Explain a start that did not create a classroom, without turning a failed condition into a pass. */
export function startMessage(result: { decision: string; missing?: string[] | null; reason?: string | null }): string {
  if (result.missing?.length) return `尚不能开始，待确认：${result.missing.map(item => startItems[item] ?? item).join('、')}。`
  return (result.reason && startReasons[result.reason]) || `尚不能开始（${result.reason ?? result.decision}）。`
}
