import type { Snapshot } from './client'

export function canConfirm(snapshot?: Pick<Snapshot, 'mode' | 'wait_kind'>): boolean {
  return snapshot?.mode === 'waiting_input' && snapshot.wait_kind === 'closing_confirmation'
}

/** Which confirmation the pop-up asks for: one per thing Jane said, so it asks again after she
 *  answers a question about it; '' when nothing is to be confirmed. */
export function confirmationKey(snapshot?: Pick<Snapshot, 'mode' | 'wait_kind' | 'wait_id' | 'presented_action'>): string {
  return snapshot && canConfirm(snapshot) ? snapshot.presented_action?.action_id ?? snapshot.wait_id : ''
}
