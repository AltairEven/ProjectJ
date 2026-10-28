// Clicked task types (ADR-0020): what the page shows and the answer it sends. The page only sees
// shuffled options, cards, right-hand items and word banks, and answers by display position.

// pictures: the option pictures' resource ids in display order, when every option has one (ADR-0026).
export type ChoiceView = { type: 'choice', version: number, variant: 'true_false' | 'true_false_not_given' | 'single', options: string[], pictures?: string[] }
export type OrderView = { type: 'order', version: number, variant: 'events' | 'words', items: string[] }
export type MatchView = { type: 'match', version: number, left: string[], right: string[] }
export type FillView = { type: 'fill', version: number, variant: 'word_bank' | 'free', text: string, blanks: number, bank: string[] | null }
export type TaskView = ChoiceView | OrderView | MatchView | FillView

export type Answer =
  | { type: 'choice', choice: number | null }
  | { type: 'order', order: number[] }
  // picked: the left item waiting for its right-hand partner.
  | { type: 'match', pairs: (number | null)[], picked: number | null }
  | { type: 'fill', blanks: string[] }

export const BLANK = '___'

/** Picture options show only once every picture loaded; one failure turns them back into words. */
export function picturesSettled(outcomes: (boolean | undefined)[]): 'loading' | 'pictures' | 'words' {
  if (!outcomes.length || outcomes.includes(false)) return 'words'
  return outcomes.every(Boolean) ? 'pictures' : 'loading'
}

export function emptyAnswer(task: TaskView): Answer {
  switch (task.type) {
    case 'choice': return { type: 'choice', choice: null }
    case 'order': return { type: 'order', order: [] }
    case 'match': return { type: 'match', pairs: task.left.map(() => null), picked: null }
    case 'fill': return { type: 'fill', blanks: Array.from({ length: task.blanks }, () => '') }
    default: { const never: never = task; return never }
  }
}

/** One option picked, every card placed, every left item paired, every blank filled. */
export function ready(task: TaskView, answer: Answer): boolean {
  if (answer.type !== task.type) return false
  switch (answer.type) {
    case 'choice': return answer.choice !== null
    case 'order': return answer.order.length === (task as OrderView).items.length
    case 'match': return answer.pairs.every(pair => pair !== null)
    case 'fill': return answer.blanks.every(blank => blank.trim() !== '')
  }
}

/** The response the core judges: {choice}, {order}, {pairs} or {blanks}. */
export function toResponse(answer: Answer): Record<string, unknown> {
  switch (answer.type) {
    case 'choice': return { choice: answer.choice }
    case 'order': return { order: answer.order }
    case 'match': return { pairs: answer.pairs }
    case 'fill': return { blanks: answer.blanks }
  }
}

/** A card goes to the end of the row; a placed card tapped again comes back. */
export function tapCard(answer: Extract<Answer, { type: 'order' }>, index: number): Answer {
  const order = answer.order.includes(index) ? answer.order.filter(item => item !== index) : [...answer.order, index]
  return { ...answer, order }
}

/** Tap a left item, then a right one; a right item belongs to one left item, so pairing it
 * again moves it. Tapping a left item that is already paired unpairs it. */
export function tapPair(answer: Extract<Answer, { type: 'match' }>, side: 'left' | 'right', index: number): Answer {
  if (side === 'left') {
    if (answer.pairs[index] !== null) return { ...answer, pairs: answer.pairs.map((pair, left) => left === index ? null : pair), picked: null }
    return { ...answer, picked: answer.picked === index ? null : index }
  }
  if (answer.picked === null) return answer
  const pairs = answer.pairs.map(pair => pair === index ? null : pair)
  pairs[answer.picked] = index
  return { ...answer, pairs, picked: null }
}

/** A word from the bank fills the first empty blank. */
export function fillWord(answer: Extract<Answer, { type: 'fill' }>, word: string): Answer {
  const at = answer.blanks.findIndex(blank => blank.trim() === '')
  if (at < 0) return answer
  return { ...answer, blanks: answer.blanks.map((blank, index) => index === at ? word : blank) }
}

export function setBlank(answer: Extract<Answer, { type: 'fill' }>, index: number, word: string): Answer {
  return { ...answer, blanks: answer.blanks.map((blank, at) => at === index ? word : blank) }
}

/** The sentence around its blanks. */
export const fillParts = (text: string) => text.split(BLANK)

/** How the student answers now: on the task, by recording, with the story-reading buttons,
 *  by typing, or not at all. */
export function answerMode(snapshot: { task?: unknown, wait_kind: string, recording?: unknown, mode: string } | undefined): 'task' | 'reading' | 'story' | 'text' | 'none' {
  if (!snapshot || snapshot.mode === 'ended' || snapshot.mode === 'offscreen') return 'none'
  if (snapshot.wait_kind === 'reading_start' || snapshot.wait_kind === 'reading') return 'story'
  const answering = ['answer', 'retry', 'clarification'].includes(snapshot.wait_kind)
  if (snapshot.task && answering) return 'task'
  if (snapshot.recording) return 'reading'
  return 'text'
}
