import type { Snapshot } from './client'

type View = Pick<Snapshot, 'core_revision' | 'mode'>
export type Cancellation = { eventId: string; command: 'stop' | 'pause'; pausedRevision?: number }

export function cancel(current: Cancellation | undefined, eventId: string, command: 'stop' | 'pause'): Cancellation {
  return current?.command === 'stop' ? current : { eventId, command }
}

export function acknowledgePause(current: Cancellation | undefined, eventId: string, snapshot: View) {
  if (current?.command !== 'pause' || current.eventId !== eventId || snapshot.mode !== 'paused') return current
  return { ...current, pausedRevision: snapshot.core_revision }
}

export function presentationCancelled(current: Cancellation | undefined, snapshot?: View): boolean {
  if (!current) return false
  // Only a running revision after this request's paused acknowledgement proves a resume.
  return current.command === 'stop' || current.pausedRevision === undefined || !snapshot ||
    snapshot.core_revision <= current.pausedRevision || snapshot.mode === 'paused' || snapshot.mode === 'ended'
}
