import type { Document as Doc, RevisionRef as Ref } from './client'
import { byNewest } from './learning.ts'

export type CourseState = '备课中' | '等你确认' | '待上课' | '已上' | '已评价'
type Shape = Pick<Doc, 'object_id' | 'revision' | 'kind' | 'body'> & { created_at?: string }

export const previewLabels: Record<string, string> = { story: '本课故事', why: '本课目的', materials: '实物准备', help: '帮助方式', exit: '时长与退出' }
export const previewRank = (key: string) => key in previewLabels ? Object.keys(previewLabels).indexOf(key) : 99

const same = (value: unknown, doc: Shape) => (value as Ref | undefined)?.object_id === doc.object_id && (value as Ref).revision === doc.revision

/** The preview of exactly this course revision; each discussion round adds a revision. */
export const previewOf = (course: Shape, objects: Shape[]) =>
  objects.find(doc => doc.kind === 'preview' && same(doc.body.course_ref, course))

function state(course: Shape, objects: Shape[]): CourseState {
  const facts = objects.filter(doc => doc.kind === 'fact' && (doc.body.course_ref as Ref | undefined)?.object_id === course.object_id)
  if (facts.length) return objects.some(doc => doc.kind === 'evaluation' && facts.some(fact => fact.body.activity_id === doc.body.activity_id)) ? '已评价' : '已上'
  const preview = previewOf(course, objects)
  if (!preview) return '备课中'
  return same((preview.body.confirmation as Record<string, unknown> | undefined)?.course_ref, course) ? '待上课' : '等你确认'
}

/** The main page offers this exact course again: its class was stopped and the evaluation waits (server rule). */
export const retakeOffered = (next: { kind: string, subject?: Record<string, unknown> | null } | null | undefined, course: Shape) =>
  next?.kind === 'evaluation' && same(next.subject?.retake, course)

/** The course's latest class: a stopped class taken again keeps both, the newer one is shown. */
export const latestFact = (course: Shape, objects: Shape[]) =>
  objects.filter(doc => doc.kind === 'fact' && (doc.body.course_ref as Ref | undefined)?.object_id === course.object_id)
    .sort(byNewest)[0] as Shape | undefined

export function courseRows(objects: Shape[]) {
  return objects.filter(doc => doc.kind === 'course')
    .sort(byNewest)
    .map(course => ({ object_id: course.object_id, revision: course.revision,
      title: String((course.body.parent_preview as Record<string, unknown> | undefined)?.story ?? '未命名的课'), state: state(course, objects) }))
}

/** The class asks for things away from the screen only when a task goes offscreen (server rule). */
export const needsMaterials = (course: Pick<Doc, 'body'>) =>
  ((course.body.tasks ?? []) as { purpose?: string }[]).some(task => task.purpose === 'offscreen')
