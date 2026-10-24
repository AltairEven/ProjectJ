// The student's words: box entries during class (by surface form) and record rows on the
// vocabulary page (by lemma), as the server computed them. Nothing here scores anything.

export type Entry = {
  surface?: string, word?: string, lemma: string | null, forms?: string[],
  part_of_speech: string | null, definition: string | null, status: string,
  audio?: boolean | Record<string, unknown> | null,
  weight?: number, mark_count?: number, exit_count?: number,
  first_marked?: string, last_marked?: string, last_seen?: string,
  mastered_activity?: string | null,
  marks?: { course_title: string, sentence: string, date: string }[], unmarked_appearances?: string[],
}
type Event = { kind: string, payload: Record<string, unknown> }

/** 'Getting ready…' while the lookup runs, 'Not ready (try again)' when it failed or never came, else 'Ready'. */
export function statusLabel(entry: { status: string, definition: string | null }): string {
  if (entry.status === 'marked' || entry.status === 'pending') return 'Getting ready…'
  return entry.definition && entry.status !== 'failed' ? 'Ready' : 'Not ready (try again)'
}

/** The lowercase surface forms still marked when the class ended, in the order first marked. */
export function markedInLesson(events: Event[]): string[] {
  const marked = new Set<string>()
  for (const event of events) {
    const word = String(event.payload.word ?? '').toLowerCase()
    if (event.kind === 'word_marked') marked.add(word)
    if (event.kind === 'word_unmarked') marked.delete(word)
  }
  return [...marked]
}

/** Every recorded attribute of one word, for the vocabulary page. */
export function entryRows(row: Entry): [string, unknown][] {
  const seen = row.unmarked_appearances ?? []
  return [
    ['Word forms', (row.forms ?? []).join(', ')],
    ['Part of speech', row.part_of_speech],
    ['Meaning', row.definition],
    // Mastered on its last appearance: last_seen is the day, the last appearance the story.
    ['Status', row.status === 'mastered' ? `Mastered on ${row.last_seen}${seen.length ? ` (${seen.at(-1)})` : ''}` : 'Learning'],
    ['First marked', row.first_marked],
    ['Last marked', row.last_marked],
    ['Times marked', row.mark_count],
    ['Last seen', row.last_seen],
    ['Seen without marking', seen.length ? `${row.exit_count} (${seen.join(', ')})` : row.exit_count],
    ['Weight', row.weight],
    ['Marked in', (row.marks ?? []).map(mark => `${mark.course_title} (${mark.date}): ${mark.sentence}`).join('\n')],
  ]
}
