import type { Document as Doc, RevisionRef as Ref } from './client'

type Body = Record<string, any>

const keyLabels: Record<string, string> = {
  report_ref: '汇总报告', evaluation_ref: '课后评价', course_ref: '课程', preview_ref: '家长预览', check_ref: '内容核对', preparation: '备课',
  start: '开始', unverified: '未验证', state_ref: '学习状态', plan_ref: '计划', plan_effect: '计划处理',
  demand_ref: '下一课需求', suggestion_ref: '下节建议', review_ref: '复盘', decision_ref: '具体方案', choice: '家长选择',
  effect: '结果', next_step: '下一步', run_id: '新运行目录', result: '结果', qualification: '使用资格',
  status: '课程状态', reason: '原因', plan_reason: '计划理由',
}
const valueLabels: Record<string, string> = {
  drafted: '已编写，待确认', pending: '待开始', actual_api: '实际API', voice: '声音',
  child_entry: '儿童入口', reused: '沿用', reused_with_reason: '沿用并说明理由', plan_effective: '新计划已生效',
  not_changed: '未改变', recheck_demand_and_course: '复核需求和课程', accept: '接受', reject: '拒绝',
  restored_to_new_simulation: '已恢复到新运行目录', not_granted: '未授予', not_ready: '课程未就绪', adjusted: '已调整',
  confirmed: '已确认', awaiting_generation: '待生成', planned: '已列入清单，确认后生成', generated: '待查看', adopted: '已采用', not_connected: '未接入',
}
const kindLabels: Record<string, string> = { image: '图片', audio: '语音', animation: '动画', video: '视频' }
const stageLabels: Record<string, string> = { question: '提问时', help: '求助后' }
const directionLabels: Record<string, string> = {
  consolidate: '巩固', review: '复查', extend: '拓展', advance: '小步进阶', observe: '短观察',
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
    case 'evaluation': return body.report?.summary
    case 'preview': return body.content?.story
    case 'suggestion': return body.direction
    case 'demand': return `${body.direction ? `${directionLabels[body.direction] ?? body.direction}：` : ''}${body.primary_goal?.description}`
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

const joined = (items: unknown) => Array.isArray(items) && items.length ? items.join('；') : '无'

/** What a planned demand or a review adds beyond its one-line summary. */
function details(ref: Ref, docs: Doc[]): [string, string][] {
  const doc = docs.find(item => item.object_id === ref.object_id && item.revision === ref.revision)
  const body = doc?.body as Body | undefined
  if (doc?.kind === 'demand' && body?.note) {
    const note = body.note
    return [['原安排', note.before], ['新安排', note.after], ['为什么', note.why], ['还不确定', joined(note.uncertain)], ['下节预期', note.expectation]]
  }
  if (doc?.kind === 'review' && body) {
    return [['仍未知', joined(body.unknown)], ...(body.checks?.length ? [['规则调整', joined(body.checks)] as [string, string]] : [])]
  }
  return []
}

/** Flatten an operation result into labelled rows; references show what the saved object says. */
export function describe(value: unknown, docs: Doc[]): [string, string][] {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return [['结果', text(value)]]
  return Object.entries(value).flatMap(([key, item]): [string, string][] => {
    if (isRef(item)) return [[keyLabels[key] ?? key, reference(item, docs)], ...details(item, docs)]
    if (typeof item === 'object' && item !== null && !Array.isArray(item)) return describe(item, docs)
    return [[keyLabels[key] ?? key, text(item)]]
  })
}

/** A model-drafted evaluation as rows: summary first, then each attempt and any rule changes. */
export function evaluationLines(doc: Doc, docs: Doc[]): [string, string][] {
  const body = doc.body as Body
  if (!body.report) return [['课后评价', '这一版评价没有模型解读。']]
  const name = (id: string) => (docs.find(item => item.kind === 'capability' && item.body.dimension_id === id)?.body.name as string) ?? id
  const rows: [string, string][] = [
    ['本课概括', body.report.summary], ['最有依据', joined(body.report.strongest)],
    ['尚未确定', joined(body.report.uncertain)], ['课程与课堂差异', body.report.alignment],
    ...(body.tasks ?? []).map((task: Body): [string, string] => [`${name(task.dimension_id)}·${task.task_id}`,
      `${judgmentLabels[task.working_judgment] ?? task.working_judgment}：${task.explanation ?? ''}`]),
  ]
  if (body.checks?.length) rows.push(['规则调整', body.checks.map((check: Body) => `${check.task_id}：${check.reason}`).join('；')])
  return rows
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
/** One line per teaching resource: kind, need, stage, purpose, Jane's words, fallback, state and versions. */
export function resourceLines(preview: Body): string[] {
  return (preview.resources ?? []).map((item: Body) => {
    const versions = item.versions?.length ? `${item.adopted_version ? `，采用第${item.adopted_version}版` : ''}，共${item.versions.length}版` : ''
    return `${kindLabels[item.kind] ?? item.kind}（${item.required ? '必需' : '可选'}，${stageLabels[item.stage] ?? item.stage}）：${item.purpose}`
      + `${item.text ? `「${item.text}」` : ''}；没有时：${item.fallback}——${valueLabels[item.state] ?? item.state}${versions}`
  })
}

/** Required resources that still have no adopted version; the class cannot start before them. */
export function missingResources(preview: Body): string[] {
  return (preview.resources ?? []).filter((item: Body) => item.required && !item.adopted_version).map((item: Body) => item.purpose)
}

/** Each discussion round as the parent's feedback and the model's note, then the confirmation. */
export function discussionLines(preview: Body): [string, string][] {
  const rounds = (preview.discussion ?? []).flatMap((item: Body, index: number): [string, string][] => [
    [`第${index + 1}轮意见`, item.feedback], [`第${index + 1}轮修改说明`, item.note],
  ])
  const confirmation = preview.confirmation
  return [...rounds, ['确认', confirmation ? `已确认（${confirmation.confirmed_at}）` : '待确认']]
}

export function startMessage(result: { decision: string; missing?: string[] | null; reason?: string | null }): string {
  if (result.missing?.length) return `尚不能开始，待确认：${result.missing.map(item => startItems[item] ?? item).join('、')}。`
  return (result.reason && startReasons[result.reason]) || `尚不能开始（${result.reason ?? result.decision}）。`
}

const purposeLabels: Record<string, string> = {
  planning: '学习规划', preparation: '备课', classroom_judgment: '课堂判断', evaluation: '课后评价',
}
const itemLabels: Record<string, string> = {
  auth: '鉴权', model: '模型', structured_output: '结构化输出', cancellation: '取消', image_input: '图片输入', audio: '音频',
  tools: '工具关闭', 'tier:fast': 'Fast层级',
}
const statusLabels: Record<string, string> = {
  verified: '已核实', unverified: '未核实', unsupported: '不支持', failed: '失败', not_probed: '未探测',
}
const itemLabel = (name: string) => itemLabels[name] ?? (name.startsWith('effort:') ? `档位${name.slice(7)}` : name)

export function modelLines(models?: Body): string[] {
  if (!models?.purposes) return ['模型接入状态未取得。']
  const head = models.initialized
    ? `模型已初始化${models.simulated ? '（模拟适配器）' : ''}。`
    : '模型未初始化，备课、开课、复盘和评价暂不可用。'
  const purposes = Object.entries(models.purposes as Record<string, Body>).map(([purpose, item]) => {
    const tier = item.binding?.tier === 'fast' ? ' · Fast' : ''
    const binding = item.binding ? `${item.binding.adapter} · ${item.binding.model} · ${item.binding.effort}${tier}` : '未配置'
    const reasons = (item.reasons ?? []) as string[]
    return `${purposeLabels[purpose] ?? purpose}：${binding}${reasons.length ? `；未通过：${reasons.join('；')}` : ''}`
  })
  // One line per capability report, items grouped by status in probe order; never credentials.
  const reports = ((models.reports ?? []) as Body[]).map(report => {
    const groups = new Map<string, string[]>()
    for (const [name, item] of Object.entries((report.items ?? {}) as Record<string, Body>)) {
      const status = statusLabels[item.status] ?? String(item.status)
      groups.set(status, [...(groups.get(status) ?? []), itemLabel(name)])
    }
    return `能力报告 ${report.adapter} · ${report.model}：${[...groups].map(([status, names]) => `${status} ${names.join('、')}`).join('；')}`
  })
  return [head, ...purposes, ...reports]
}
