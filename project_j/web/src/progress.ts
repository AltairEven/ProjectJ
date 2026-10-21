// How far things are, for the progress bars: the class's tasks and a background job's items.

/** The goal comes first (task_index -1), then task n of N, then the closing. */
export function classProgress({ task_index: index, task_count: count, wait_kind: wait }: { task_index: number, task_count: number, wait_kind: string }) {
  const value = Math.min(Math.max(index + 1, 0), count), max = Math.max(count, 1)
  if (wait === 'closing_confirmation' || index >= count) return { label: '收尾', value, max }
  return { label: index < 0 ? '开场' : `第${index + 1}/${count}题`, value, max }
}

/** "2/5" from a job fills its bar; a job without a count keeps a moving bar. */
export function jobProgress(progress?: string | null) {
  const match = /^(\d+)\/(\d+)$/.exec(progress ?? '')
  return match ? { value: Number(match[1]), max: Number(match[2]) } : undefined
}

/** One box a task, as in an exercise book: ticked when done, marked while current. */
export const taskMarks = (index: number, count: number): ('done' | 'now' | 'todo')[] =>
  Array.from({ length: count }, (_, task) => task < index ? 'done' : task === index ? 'now' : 'todo')
