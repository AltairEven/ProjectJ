import type { Snapshot } from './client'

export function canConfirm(snapshot?: Pick<Snapshot, 'mode' | 'wait_kind'>): boolean {
  return snapshot?.mode === 'waiting_input' &&
    (snapshot.wait_kind === 'goal_confirmation' || snapshot.wait_kind === 'closing_confirmation')
}
