import { useState } from 'react'
import { api, uid } from '../api'
import type { Document as Doc } from '../client/types.gen'
import { byNewest } from '../learning'
import { focusError, initialFocus, nextStepView } from '../next-step'
import { endLabels } from '../report'
import { JobLine, Rows, useRun, type PageProps } from '../ui'

type Skill = { skill_id: string, group: string, label: string }
type Suggestion = { skill_id: string, reason: string }
type Topic = { title: string, reason: string }

// The topic and skills of the next lesson, chosen before Jane prepares it (preparation waits for this).
function FocusPicker({ token, learner, refresh, subject }: Pick<PageProps, 'token' | 'learner' | 'refresh'> & { subject: Record<string, any> }) {
  const { busy, run, status } = useRun(refresh)
  const options: Skill[] = subject.options ?? []
  const suggestions: Suggestion[] = subject.suggestions ?? []
  const topics: Topic[] = subject.topics ?? []
  const [choice, setChoice] = useState<number | 'own' | null>(topics.length ? null : 'own')
  const [own, setOwn] = useState('')
  const [picked, setPicked] = useState(() => initialFocus(suggestions))
  const [note, setNote] = useState('')
  const topic = choice === 'own' ? own.trim() : choice === null ? '' : topics[choice].title
  const error = focusError(picked.length, note, topic)
  const reasons = new Map(suggestions.map(item => [item.skill_id, item.reason]))
  const toggle = (id: string, on: boolean) => setPicked(current => on ? [...current, id] : current.filter(item => item !== id))
  const send = () => {
    const body = { request_id: uid(), learner_id: learner, demand_ref: subject.demand_ref, topic, skills: picked, note: note.trim() || null }
    void run(async () => { await api(token, 'focus', body) }, '保存选择')
  }
  return <>
    <p>{topics.length ? 'Jane 按你的兴趣想了几个题材，也列出了可以练的知识点。' : '写下这节课想读的题材，再勾选想练的知识点。'}选好交给 Jane，她才开始备课。</p>
    <fieldset className="skills"><legend>{topics.length ? '题材（选一个）' : '题材'}</legend>
      {topics.map((item, index) => <label key={index} className="check">
        <input type="radio" name="topic" checked={choice === index} onChange={() => setChoice(index)} />
        {item.title}<span className="tag">{item.reason}</span></label>)}
      {topics.length > 0 && <label className="check"><input type="radio" name="topic" checked={choice === 'own'} onChange={() => setChoice('own')} />我想读别的：</label>}
      <input aria-label="我想读的题材" placeholder="写一句你想读的内容" value={own} onFocus={() => setChoice('own')} onChange={event => setOwn(event.target.value)} /></fieldset>
    <p className="hint">知识点（最多 3 项，可以不选）</p>
    {[...new Set(options.map(item => item.group))].map(group => <fieldset key={group} className="skills"><legend>{group}</legend>
      {options.filter(item => item.group === group).map(item => <label key={item.skill_id} className="check">
        <input type="checkbox" checked={picked.includes(item.skill_id)} onChange={event => toggle(item.skill_id, event.target.checked)} />
        {item.label}{reasons.has(item.skill_id) && <span className="tag">Jane建议：{reasons.get(item.skill_id)}</span>}</label>)}</fieldset>)}
    <label>其他想练的（可选）<input value={note} onChange={event => setNote(event.target.value)} /></label>
    {error && <p className={topic ? 'notice' : 'hint'}>{error}</p>}
    {status}
    <div className="row"><button disabled={busy || error !== null} onClick={send}>交给 Jane，开始备课</button></div>
  </>
}

export default function Home({ token, dashboard, learner, go, refresh }: PageProps) {
  const { busy, run, status } = useRun(refresh)
  const view = nextStepView(dashboard.next ?? { kind: 'none' })
  const objects = dashboard.objects ?? []
  const jobs = dashboard.jobs ?? []
  const fact = objects.filter(doc => doc.kind === 'fact').sort(byNewest)[0] as Doc | undefined
  const activity = fact && String(fact.body.activity_id)
  const course = fact && objects.find(doc => doc.kind === 'course' && doc.object_id === (fact.body.course_ref as Doc).object_id)
  const evaluation = activity === undefined ? undefined : objects.find(doc => doc.kind === 'evaluation' && doc.body.activity_id === activity)
  const week = objects.find(doc => doc.kind === 'plan' && doc.body.level === 'week')
  const retry = (name: string) => run(async () => { await api(token, 'jobs/retry', { learner_id: learner, name }) })
  return <>
    <section className="next"><h2>下一步</h2><p className="next-title">{view.title}</p>
      {(view.action || view.also) && <div className="row">
        {view.action && <button onClick={() => go(view.page, view.detail)}>{view.action}</button>}
        {view.also && <button className="quiet" onClick={() => go(view.also!.page, view.also!.detail)}>{view.also.action}</button>}</div>}
      {dashboard.next?.kind === 'focus' && <FocusPicker token={token} learner={learner} refresh={refresh} subject={dashboard.next.subject ?? {}} />}</section>
    {status}
    <section><h2>后台进行中</h2>
      {jobs.length ? <ul className="list jobs">{jobs.map(job => <li key={job.name}><JobLine job={job} />
        {/* A failed confirm or start is retried from its course page. */}
        {job.state === 'failed' && !job.name.startsWith('start:') && !job.name.startsWith('confirm:') &&
          <button className="quiet" disabled={busy} onClick={() => void retry(job.name)}>重试</button>}</li>)}</ul>
        : <p>没有正在进行的事。</p>}</section>
    <section><h2>最近一课</h2>{fact ? <>
      <Rows rows={[
        ['课程', (course?.body.parent_preview as Record<string, string> | undefined)?.story],
        ['结束', endLabels[String((fact.body.end as Record<string, unknown>)?.reason)] ?? '未知'],
        ['评价', (evaluation?.body.report as Record<string, string> | undefined)?.summary ?? '评价中或尚未生成'],
      ]} />
      <button className="quiet" onClick={() => go('evaluations', activity)}>查看评价</button></> : <p>还没有上过课。</p>}</section>
    <section><h2>本周计划</h2>{week ? <Rows rows={[
      ['主要目标', (week.body.main_goal as Record<string, string>).description],
      ['下次复盘', week.body.review_at ? new Date(String(week.body.review_at)).toLocaleDateString() : '未定'],
    ]} /> : <p>还没有学习计划。</p>}</section>
  </>
}
