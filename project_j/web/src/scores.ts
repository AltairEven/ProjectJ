// Scores over lessons (ADR-0020): the server averages them; the page only words them.
import { dateText } from './dates.ts'
import { capabilityName, type Shape } from './report.ts'

type Average = { average: number | null, count: number }
export type LessonScore = {
  activity_id: string, course_ref: { object_id: string, revision: number }, occurred_at: string,
  by_type: Record<string, Average>, by_dimension: Record<string, Average>, unknown: number,
}
/** The last lessons pooled question by question by the server, as planning and preparation read them. */
export type RecentScores = { lessons: number, by_dimension: Record<string, Average> }

export const typeNames: Record<string, string> = {
  choice: 'Choose', order: 'Order', match: 'Match', fill: 'Fill in', answer: 'Short answer', open: 'Open question', read_aloud: 'Read aloud',
}
const nameOf = (catalog: Shape[], id: string) => capabilityName(catalog, id) ?? id

function averages(groups: Record<string, Average>, name: (key: string) => string): string {
  const known = Object.entries(groups).filter(([, item]) => item.average !== null)
  return known.length ? known.map(([key, item]) => `${name(key)} ${item.average!.toFixed(1)} (${questions(item.count)})`).join(', ') : 'No known scores'
}

const questions = (count: number) => `${count} question${count === 1 ? '' : 's'}`

/** One row per lesson, the newest first: its story, then averages by task type and by ability. */
export function scoreRows(scores: LessonScore[], objects: Shape[]) {
  return [...scores].reverse().map(lesson => {
    const course = objects.find(doc => doc.kind === 'course' && doc.object_id === lesson.course_ref.object_id)
    const unknown = lesson.unknown ? `; ${questions(lesson.unknown)} unknown` : ''
    return {
      key: lesson.activity_id,
      date: dateText(lesson.occurred_at),
      title: String(course?.body.parent_preview?.story ?? 'A lesson'),
      types: averages(lesson.by_type, key => typeNames[key] ?? key) + unknown,
      dimensions: averages(lesson.by_dimension, key => nameOf(objects, key)),
    }
  })
}

/** The last lessons by ability, in the numbers the server pooled for planning and preparation. */
export function recentLine(recent: RecentScores, catalog: Shape[]): string {
  return `Last ${recent.lessons} lesson${recent.lessons === 1 ? '' : 's'}: ${averages(recent.by_dimension, key => nameOf(catalog, key))}`
}
