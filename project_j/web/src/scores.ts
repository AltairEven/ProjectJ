// Scores over lessons (ADR-0020): the server averages them; the page only words them.

type Body = Record<string, any>
type Shape = { object_id: string, kind: string, body: Body }
type Average = { average: number | null, count: number }
export type LessonScore = {
  activity_id: string, course_ref: { object_id: string, revision: number }, occurred_at: string,
  by_type: Record<string, Average>, by_dimension: Record<string, Average>, unknown: number,
}

export const typeNames: Record<string, string> = {
  choice: '选择', order: '排序', match: '配对', fill: '填空', answer: '精确问答', open: '开放问答', read_aloud: '朗读',
}
const nameOf = (catalog: Shape[], id: string) =>
  (catalog.find(doc => doc.kind === 'capability' && doc.body.dimension_id === id)?.body.name as string | undefined) ?? id

function averages(groups: Record<string, Average>, name: (key: string) => string): string {
  const known = Object.entries(groups).filter(([, item]) => item.average !== null)
  return known.length ? known.map(([key, item]) => `${name(key)} ${item.average!.toFixed(1)}（${item.count}题）`).join('、') : '没有已知分数'
}

const day = (iso: string) => { const at = new Date(iso); return `${at.getMonth() + 1}月${at.getDate()}日` }

/** One row per lesson, the newest first: its story, then averages by task type and by ability. */
export function scoreRows(scores: LessonScore[], objects: Shape[]) {
  return [...scores].reverse().map(lesson => {
    const course = objects.find(doc => doc.kind === 'course' && doc.object_id === lesson.course_ref.object_id)
    const unknown = lesson.unknown ? `；${lesson.unknown}题未知` : ''
    return {
      key: lesson.activity_id,
      date: day(lesson.occurred_at),
      title: String(course?.body.parent_preview?.story ?? '一节课'),
      types: averages(lesson.by_type, key => typeNames[key] ?? key) + unknown,
      dimensions: averages(lesson.by_dimension, key => nameOf(objects, key)),
    }
  })
}

/** The last five lessons pooled by ability, as the planner and preparation see them. */
export function recentLine(scores: LessonScore[], catalog: Shape[]): string {
  const latest = scores.slice(-5), pooled: Record<string, { sum: number, count: number }> = {}
  for (const lesson of latest) for (const [key, item] of Object.entries(lesson.by_dimension)) {
    if (item.average === null) continue
    pooled[key] ??= { sum: 0, count: 0 }
    pooled[key].sum += item.average * item.count; pooled[key].count += item.count
  }
  const parts = Object.entries(pooled).map(([key, item]) => `${nameOf(catalog, key)} ${(item.sum / item.count).toFixed(1)}（${item.count}题）`)
  return `最近${latest.length}节课：${parts.length ? parts.join('、') : '没有已知分数'}`
}
