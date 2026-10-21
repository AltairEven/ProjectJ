import { useState } from 'react'
import { api, show, uid } from '../api'
import type { Document as Doc } from '../client/types.gen'
import { byNewest, goalCard } from '../learning'
import { describe } from '../report'
import { Rows, useRun, type PageProps } from '../ui'

const ref = (doc: Doc) => ({ object_id: doc.object_id, revision: doc.revision })
const levels = [['long_term', '长期'], ['annual', '年度'], ['stage', '阶段'], ['week', '本周']] as const

/** A changed goal or new capabilities wait for the student before any course is prepared. */
export function GoalConfirmation({ token, learner, refresh, demand, catalog }: Pick<PageProps, 'token' | 'learner' | 'refresh'> & { demand: Doc, catalog: Doc[] }) {
  const { busy, run, status } = useRun(refresh)
  const card = goalCard(demand, catalog)
  const decide = (choice: 'accept' | 'reject') => { const requestId = uid(); void run(async () => {
    await api(token, 'goal/decide', { request_id: requestId, learner_id: learner, demand_ref: ref(demand), choice })
  }, choice === 'accept' ? '确认新目标' : '按原目标重新规划') }
  return <section className="next"><h2>新目标等你确认</h2>
    <Rows rows={[['主要目标', card.primary], ['辅助目标', card.auxiliary], ['为什么', card.reason],
      ...card.newCapabilities.map((line, index): [string, string] => [`新能力条目${index + 1}`, line])]} />
    {status}
    <div className="row"><button disabled={busy} onClick={() => decide('accept')}>同意</button>
      <button className="quiet" disabled={busy} onClick={() => decide('reject')}>不同意，沿用原目标</button></div></section>
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
      return <section key={proposal.object_id}><h2>待决定的方案</h2>
        {stale && <p className="notice">计划或状态已在此方案之后更新，这一版只能拒绝。</p>}
        <Rows rows={[['方案内容', (proposal.body.patch as { main_goal?: { description?: string } } | null)?.main_goal?.description ?? show(proposal.body.patch)],
          ['理由', proposal.body.reason], ['影响', proposal.body.impact],
          ['其他选择', (proposal.body.alternatives as string[] | undefined)?.join('；')], ['复核条件', proposal.body.review_condition]]} />
        <div className="row">{(['accept', 'reject'] as const).map(choice => <button className="quiet" key={choice} disabled={busy || (stale && choice === 'accept')}
          onClick={() => { const requestId = uid(); void run(async () => {
            setResult(await api(token, 'decision', { request_id: requestId, learner_id: learner, decision_ref: ref(proposal), choice }))
            if (choice === 'accept') await api(token, 'recheck-demand', { request_id: `${requestId}:demand`, learner_id: learner })
          }, choice === 'accept' ? '接受方案' : '拒绝方案') }}>{choice === 'accept' ? '同意' : '不同意'}</button>)}</div></section>
    })}
    <section><h2>下一课</h2>{demand ? <Rows rows={describe({ demand_ref: ref(demand) }, objects)} /> : <p>还没有下一课需求。</p>}</section>
    <section><h2>各层计划</h2>{plans.length ? <Rows rows={levels.flatMap(([level, label]) => plans.filter(doc => doc.body.level === level).map((doc): [string, string] =>
      [label, `${(doc.body.main_goal as { description: string }).description}${doc.body.review_at ? `（${new Date(String(doc.body.review_at)).toLocaleDateString()}复盘）` : ''}`]))} />
      : <p>还没有学习计划。</p>}</section>
    <section><h2>复盘</h2><p>到期时 Jane 会自动复盘；也可以现在发起。日期到了不等于计划完成。</p>
      <div className="row">{levels.slice().reverse().map(([level, label]) => <button className="quiet" key={level} disabled={busy}
        onClick={() => { const requestId = uid(); void run(async () => setResult(await api(token, 'review', { request_id: requestId, learner_id: learner, level })), `${label}复盘`) }}>{label}复盘</button>)}</div>
      {result !== undefined && <Rows rows={describe(result, objects)} />}
      {reviews.length > 0 && <Rows rows={reviews.map((doc): [string, unknown] => [new Date(doc.created_at).toLocaleDateString(), doc.body.conclusion])} />}</section>
  </>
}
