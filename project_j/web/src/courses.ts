import type { Document as Doc, RevisionRef as Ref } from './client'
import { byNewest, CHALLENGES } from './learning.ts'
import type { Shape } from './report.ts'

export type CourseState = 'Being made' | 'Waiting for you' | 'Ready' | 'Taken' | 'Evaluated'

export const previewLabels: Record<string, string> = { story: 'Story', why: 'Why this lesson', materials: 'Things to get ready', help: 'Help', exit: 'Time and stopping' }
export const previewRank = (key: string) => key in previewLabels ? Object.keys(previewLabels).indexOf(key) : 99

const same = (value: unknown, doc: Shape) => (value as Ref | undefined)?.object_id === doc.object_id && (value as Ref).revision === doc.revision

/** The preview of exactly this course revision; each discussion round adds a revision. */
export const previewOf = (course: Shape, objects: Shape[]) =>
  objects.find(doc => doc.kind === 'preview' && same(doc.body.course_ref, course))

/** The resource records of exactly this course revision, in the course's order (ADR-0027). */
export const resourcesOf = (course: Shape, objects: Shape[]) =>
  objects.filter(doc => doc.kind === 'resource' && same(doc.body.course_ref, course))
    .sort((a, b) => Number(a.body.order) - Number(b.body.order))

/** The lesson folder record, once this course revision's resources are confirmed. */
export const lessonOf = (course: Shape, objects: Shape[]) =>
  objects.find(doc => doc.kind === 'lesson' && same(doc.body.course_ref, course))

function state(course: Shape, objects: Shape[]): CourseState {
  const facts = objects.filter(doc => doc.kind === 'fact' && (doc.body.course_ref as Ref | undefined)?.object_id === course.object_id)
  if (facts.length) return objects.some(doc => doc.kind === 'evaluation' && facts.some(fact => fact.body.activity_id === doc.body.activity_id)) ? 'Evaluated' : 'Taken'
  const preview = previewOf(course, objects)
  if (!preview) return 'Being made'
  return same((preview.body.confirmation as Record<string, unknown> | undefined)?.course_ref, course) ? 'Ready' : 'Waiting for you'
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
      title: String((course.body.parent_preview as Record<string, unknown> | undefined)?.story ?? 'Untitled lesson'), state: state(course, objects) }))
}

/** The class asks for things away from the screen only when a task goes offscreen (server rule). */
export const needsMaterials = (course: Pick<Doc, 'body'>) =>
  ((course.body.tasks ?? []) as { purpose?: string }[]).some(task => task.purpose === 'offscreen')

/** The Lexile the story aimed at; a target, never a measurement (ADR-0023). */
export function storyLevelLine(course: Pick<Shape, 'body'>) {
  const level = course.body.story_level as { lexile: string, challenge: string | null, target: string } | null | undefined
  if (!level) return null
  const step = CHALLENGES.find(([name]) => name === level.challenge)?.[2] ?? 0
  return `Aimed at about ${level.target} (your level ${level.lexile}${step ? ` + ${step}L` : ''}); not measured`
}
