import { useState } from 'react'
import { api, show, uid } from '../api'
import type { Document as Doc } from '../client/types.gen'
import { dateText } from '../dates'
import { byNewest, goalCard } from '../learning'
import { describe } from '../report'
import { Rows, useRun, type PageProps } from '../ui'

const ref = (doc: Doc) => ({ object_id: doc.object_id, revision: doc.revision })
const levels = [['long_term', 'Long term'], ['annual', 'This year'], ['stage', 'This stage'], ['week', 'This week']] as const

/** A changed goal or new capabilities wait for the student before any course is prepared. */
export function GoalConfirmation({ token, learner, refresh, demand, catalog }: Pick<PageProps, 'token' | 'learner' | 'refresh'> & { demand: Doc, catalog: Doc[] }) {
  const { busy, run, status } = useRun(refresh)
  const card = goalCard(demand, catalog)
  const decide = (choice: 'accept' | 'reject') => { const requestId = uid(); void run(async () => {
    await api(token, 'goal/decide', { request_id: requestId, learner_id: learner, demand_ref: ref(demand), choice })
  }, choice === 'accept' ? 'Confirming the new goal' : 'Planning again with the old goals') }
  return <section className="next"><h2>A new goal is waiting for you</h2>
    <Rows rows={[['Main goal', card.primary], ['Second goal', card.auxiliary], ['Why', card.reason],
      ...card.newCapabilities.map((line, index): [string, string] => [`New skill ${index + 1}`, line])]} />
    {status}
    <div className="row"><button disabled={busy} onClick={() => decide('accept')}>Agree</button>
      <button className="quiet" disabled={busy} onClick={() => decide('reject')}>No, keep the old goals</button></div></section>
}

export default function Plan({ token, dashboard, learner, refresh }: PageProps) {
  const { busy, run, status } = useRun(refresh)
  const [result, setResult] = useState<unknown>()
  const objects = dashboard.objects ?? []
  const catalog = objects.filter(doc => doc.kind === 'capability')
  const demand = objects.find(doc => doc.kind === 'demand')
  const plans = objects.filter(doc => doc.kind === 'plan')
  const reviews = objects.filter(doc => doc.kind === 'review').sort(byNewest)
  const decisions = objects.filter(doc => doc.kind === 'decision' && doc.body.decision_state === 'pending')
  return <>
    {demand?.body.goal_confirmation === 'needed' && <GoalConfirmation token={token} learner={learner} refresh={refresh} demand={demand} catalog={catalog} />}
    {status}
    {decisions.map(proposal => {
      // Only current revisions are listed, so a baseline missing from them has changed since the proposal.
      const stale = [proposal.body.plan_ref, proposal.body.state_ref].some(target => !objects.some(doc =>
        doc.object_id === (target as Doc).object_id && doc.revision === (target as Doc).revision))
      return <section key={proposal.object_id}><h2>A plan change to decide</h2>
        {stale && <p className="notice">The plan or progress changed after this proposal, so it can only be rejected.</p>}
        <Rows rows={[['Change', (proposal.body.patch as { main_goal?: { description?: string } } | null)?.main_goal?.description ?? show(proposal.body.patch)],
          ['Why', proposal.body.reason], ['What it changes', proposal.body.impact],
          ['Other choices', (proposal.body.alternatives as string[] | undefined)?.join('; ')], ['Check again when', proposal.body.review_condition]]} />
        <div className="row">{(['accept', 'reject'] as const).map(choice => <button className="quiet" key={choice} disabled={busy || (stale && choice === 'accept')}
          onClick={() => { const requestId = uid(); void run(async () => {
            setResult(await api(token, 'decision', { request_id: requestId, learner_id: learner, decision_ref: ref(proposal), choice }))
            if (choice === 'accept') await api(token, 'recheck-demand', { request_id: `${requestId}:demand`, learner_id: learner })
          }, choice === 'accept' ? 'Accepting' : 'Rejecting') }}>{choice === 'accept' ? 'Agree' : 'Disagree'}</button>)}</div></section>
    })}
    <section><h2>Next lesson</h2>{demand ? <Rows rows={describe({ demand_ref: ref(demand) }, objects)} /> : <p>No next lesson plan yet.</p>}</section>
    <section><h2>Plans</h2>{plans.length ? <Rows rows={levels.flatMap(([level, label]) => plans.filter(doc => doc.body.level === level).map((doc): [string, string] =>
      [label, `${(doc.body.main_goal as { description: string }).description}${doc.body.review_at ? ` (review on ${dateText(String(doc.body.review_at))})` : ''}`]))} />
      : <p>No plan yet.</p>}</section>
    <section><h2>Review</h2><p>Jane reviews a plan when it is due; you can also start one now. A due date does not mean the plan is done.</p>
      <div className="row">{levels.slice().reverse().map(([level, label]) => <button className="quiet" key={level} disabled={busy}
        onClick={() => { const requestId = uid(); void run(async () => setResult(await api(token, 'review', { request_id: requestId, learner_id: learner, level })), `Reviewing: ${label}`) }}>Review: {label}</button>)}</div>
      {result !== undefined && <Rows rows={describe(result, objects)} />}
      {reviews.length > 0 && <Rows rows={reviews.map((doc): [string, unknown] => [dateText(doc.created_at), doc.body.conclusion])} />}</section>
  </>
}
