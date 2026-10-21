import { useState } from 'react'
import { api, uid } from '../api'
import type { Document as Doc } from '../client/types.gen'
import { evaluationRows } from '../learning'
import { endLabels, evaluationLines } from '../report'
import { Rows, useRun, type PageProps } from '../ui'

const verdicts: Record<string, string> = { correct: '正确', incorrect: '不正确', unclear: '不清楚' }
const helpNames: Record<string, string> = { light_prompt: '轻提示', scaffold: '支架', full_model: '完整示范' }
type Attempt = { task_id: string, judgment: string, inputs?: { text?: string }[], help?: Record<string, string>, pronunciation?: { status?: string } | null }

const storyOf = (fact: Doc, objects: Doc[]) => String((objects.find(doc => doc.kind === 'course' && doc.object_id === (fact.body.course_ref as Doc).object_id)
  ?.body.parent_preview as Record<string, unknown> | undefined)?.story ?? '课堂')

function attemptRows(fact: Doc): [string, string][] {
  return ((fact.body.attempts ?? []) as Attempt[]).map(attempt => {
    const said = (attempt.inputs ?? []).map(input => input.text).filter(Boolean).join(' / ') || (attempt.pronunciation ? '朗读录音' : '无')
    const help = Object.values(attempt.help ?? {}).map(level => helpNames[level] ?? level).join('、')
    return [attempt.task_id, `原答：${said}；课堂判断：${verdicts[attempt.judgment] ?? attempt.judgment}${help ? `；帮助：${help}` : ''}`]
  })
}

function Detail({ token, dashboard, learner, go, refresh, fact }: PageProps & { fact: Doc }) {
  const { busy, run, setMessage, status } = useRun(refresh)
  const [note, setNote] = useState('')
  const objects = dashboard.objects ?? []
  const activity = String(fact.body.activity_id)
  const row = evaluationRows(objects).find(item => item.activity_id === activity)
  const evaluation = objects.find(doc => doc.kind === 'evaluation' && doc.body.activity_id === activity)
  const notes = objects.filter(doc => doc.kind === 'feedback' && doc.body.activity_id === activity)
    .sort((a, b) => a.source_revision - b.source_revision)
  const send = (objection: boolean, label = objection ? '提出异议' : '补记') => { const body = { request_id: uid(), learner_id: learner, activity_id: activity, text: note.trim(), objection }
    void run(async () => { await api(token, 'evaluation/feedback', body); setNote(''); setMessage('已保存，Jane 会据此重新评价。') }, label) }
  return <>
    <button className="quiet" onClick={() => go('evaluations')}>← 全部评价</button>
    <section><h2>{storyOf(fact, objects)}</h2>
      <Rows rows={[['状态', row?.state], ['结束', endLabels[String((fact.body.end as Record<string, unknown>)?.reason)] ?? '未知']]} />
      <h3>每题原答与帮助</h3><Rows rows={attemptRows(fact)} />
      {row?.state !== '评价中' && evaluation ? <><h3>评价</h3><Rows rows={evaluationLines(evaluation, objects)} /></> : <p>评价生成中，页面会自动更新。</p>}
      {notes.length > 0 && <><h3>你的补记</h3><Rows rows={notes.map((doc, index): [string, unknown] =>
        [`第${index + 1}条${(doc.body.corrections as unknown[] | undefined)?.length ? '（异议）' : ''}`, doc.body.text])} /></>}</section>
    {status}
    {row && row.state !== '已认可' && <section><h2>补记或异议</h2>
      <label>课上没记下的帮助、离开屏幕的结果，或你不同意的地方<textarea value={note} onChange={event => setNote(event.target.value)} /></label>
      <div className="row"><button className="quiet" disabled={busy || !note.trim()} onClick={() => send(false)}>补记</button>
        <button className="quiet" disabled={busy || !note.trim()} onClick={() => send(true)}>提出异议</button>
        {row.evaluation_ref && <button disabled={busy} onClick={() => { const body = { request_id: uid(), learner_id: learner, activity_id: activity, evaluation_ref: row.evaluation_ref }
          void run(async () => { await api(token, 'evaluation/acknowledge', body) }, '认可评价') }}>认可这份评价</button>}</div></section>}
    {row?.state === '已认可' && evaluation && <section><h2>更正</h2>
      <p>发现记错的地方可以追加更正：Jane 会按更正重新评价，评完请再认可一次；原评价和记录都保留。</p>
      <label>更正说明<textarea value={note} onChange={event => setNote(event.target.value)} /></label>
      <button className="quiet" disabled={busy || !note.trim()} onClick={() => send(true, '追加更正')}>追加更正</button></section>}
  </>
}

export default function Evaluations(props: PageProps) {
  const { dashboard, detail, go } = props
  const objects = dashboard.objects ?? []
  const fact = detail ? objects.find(doc => doc.kind === 'fact' && doc.body.activity_id === detail) : undefined
  if (fact) return <Detail {...props} fact={fact} />
  const facts = new Map(objects.filter(doc => doc.kind === 'fact').map(doc => [String(doc.body.activity_id), doc]))
  const rows = evaluationRows(objects)
  return <section><h2>课后评价</h2>{rows.length ? <ul className="list">{rows.map(row => <li key={row.activity_id}>
    <button className="link" onClick={() => go('evaluations', row.activity_id)}>{storyOf(facts.get(row.activity_id)!, objects)}</button><span className="tag">{row.state}</span></li>)}</ul>
    : <p>还没有课后评价。</p>}</section>
}
