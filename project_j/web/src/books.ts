// Books expanded from taken lessons (application/books.py): one line each and when a new one may start.
import type { BookRow, Dashboard, RevisionRef } from './client/types.gen'

/** The books made from this version of the lesson. */
export const booksOf = (course: RevisionRef, books: BookRow[]): BookRow[] =>
  books.filter(book => book.course_ref.object_id === course.object_id && book.course_ref.revision === course.revision)

/** "Title · 600L · 3,120 words · 5 chapters" when done; otherwise how far it is. */
export function bookLine(book: BookRow): string {
  if (book.state === 'failed') return `Failed: ${book.reason ?? 'reason unknown'}`
  if (book.state === 'drawing') return 'Drawing pictures'
  if (book.state === 'writing') return book.planned ? `Writing chapter ${book.chapters.length + 1} of ${book.planned}` : 'Planning'
  if (book.state === 'revising') return `${book.title} · Rewriting a chapter`
  return [book.title, book.target, `${book.words.toLocaleString('en-US')} words`, `${book.chapters.length} chapters`]
    .filter(Boolean).join(' · ')
}

/** Why a book cannot be started, retried or revised now; null when it can. */
export function expandBlocked(dashboard: Pick<Dashboard, 'jobs' | 'active'>): string | null {
  if (dashboard.active) return 'A class is running.'
  const running = (dashboard.jobs ?? []).filter(job => job.state === 'running').map(job => job.name)
  if (running.some(name => name.startsWith('reading:'))) return 'A book is being written; wait until it is done.'
  if (running.includes('reference')) return 'The reference check is running; wait until it is done.'
  return null
}
