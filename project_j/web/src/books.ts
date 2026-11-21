// Books expanded from taken lessons (application/books.py): the bookshelf's lines and addresses, and when a book may be started or edited.
import type { BookChanges, BookRow, Dashboard, RevisionRef } from './client/types.gen'

export type ShelfMode = 'read' | 'edit'
const count = (n: number, one: string) => `${n.toLocaleString('en-US')} ${one}${n === 1 ? '' : 's'}`
const joined = (parts: (string | null | undefined)[]) => parts.filter(Boolean).join(' · ')

/** The books made from this version of the lesson. */
export const booksOf = (course: RevisionRef, books: BookRow[]): BookRow[] =>
  books.filter(book => book.course_ref.object_id === course.object_id && book.course_ref.revision === course.revision)

/** How far the book (its draft when it has one) is, without its title: "600L · 3,120 words · 5 chapters" when done. */
export function bookLine(book: BookRow): string {
  if (book.state === 'outlining') return book.version ? `Outline, version ${book.version}` : 'Planning the outline'
  if (book.state === 'failed') return `Failed: ${book.reason ?? 'reason unknown'}`
  if (book.state === 'drawing') return 'Drawing pictures'
  if (book.state === 'writing') return `Writing chapter ${book.chapters.length + 1} of ${book.planned}`
  if (book.state === 'revising') return `Rewriting ${book.revising ? `chapter ${book.revising}` : 'a chapter'}`
  return joined([book.target, count(book.words, 'word'), count(book.chapters.length, 'chapter')])
}

/** The saved title, else the outline's; a book still waiting for its first outline is a New book. */
export const bookTitle = (book: BookRow): string => book.saved?.title ?? book.title ?? book.outline?.title ?? 'New book'

/** One shelf row: Read opens only a saved book (and offers its download), a never-saved one is greyed and says so; Edit opens every book. */
export function shelfItem(book: BookRow, mode: ShelfMode): { line: string, opens: ShelfMode | null, unsaved: boolean } {
  if (mode === 'edit') return { line: bookLine(book), opens: 'edit', unsaved: book.draft ?? false }
  if (!book.saved) return { line: joined([bookLine(book), 'Not saved yet']), opens: null, unsaved: false }
  return { line: joined([book.target, count(book.saved.words, 'word'), count(book.saved.chapters, 'chapter')]), opens: 'read', unsaved: false }
}

/** "chapter 2 rewritten, 1 new chapter, 2 new pictures": what a save would put into the book. */
export function changesLine({ rewritten, added, drawn }: BookChanges): string {
  const parts = [
    rewritten.length ? `${rewritten.length === 1 ? 'chapter' : 'chapters'} ${rewritten.join(', ')} rewritten` : '',
    added ? count(added, 'new chapter') : '',
    drawn ? count(drawn, 'new picture') : '',
  ].filter(Boolean)
  return parts.length ? parts.join(', ') : 'No changes yet'
}

/** The draft's buttons: why Save waits (null: it can be saved), whether there is a saved book to go back to, whether a chapter can be previewed. */
export function draftActions(book: BookRow): { reason: string | null, revert: boolean, preview: boolean } | null {
  if (!book.draft) return null
  const reason = book.continuing ? 'Write or discard the new chapters first' : book.state === 'ready' ? null : 'Wait until it is written'
  return { reason, revert: !!book.saved, preview: book.chapters.length > 0 }
}

/** The chapters before and after this one, null at either end. */
export const neighbours = (index: number, count: number): { previous: number | null, next: number | null } =>
  ({ previous: index > 0 ? index - 1 : null, next: index < count - 1 ? index + 1 : null })

/** A bookshelf address: read:<book> (saved), draft:<book> (preview) or edit:<book>. */
export function bookView(detail?: string): { mode: 'read' | 'edit' | 'draft', book: string } | null {
  const [mode, book] = (detail ?? '').split(':')
  return (mode === 'read' || mode === 'edit' || mode === 'draft') && book ? { mode, book } : null
}

/** The outline being discussed (a new book's, or a finished book's new chapters): planned now, or waiting at its version; null when there is none. */
export function outlineLine(book: BookRow, running: boolean): string | null {
  if (running) return book.state === 'outlining' || book.state === 'ready' ? 'Planning the outline' : null
  return book.state === 'outlining' || book.continuing ? `Outline, version ${book.version}` : null
}

/** Why a book cannot be started, retried or revised now; null when it can. */
export function expandBlocked(dashboard: Pick<Dashboard, 'jobs' | 'active'>): string | null {
  if (dashboard.active) return 'A class is running.'
  const running = (dashboard.jobs ?? []).filter(job => job.state === 'running').map(job => job.name)
  if (running.some(name => name.startsWith('reading:'))) return 'A book is being written; wait until it is done.'
  if (running.includes('reference')) return 'The reference check is running; wait until it is done.'
  return null
}

/** Difficulty steps exist only with a Lexile in the profile; without one a book keeps the story's own level. */
export const levelled = (targets: Record<string, string>): boolean => Object.keys(targets).length > 0
