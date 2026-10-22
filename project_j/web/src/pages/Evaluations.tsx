import { useState } from 'react'
import { api, uid } from '../api'
import type { Document as Doc } from '../client/types.gen'
import { evaluationRows } from '../learning'
import { endLabels, evaluationLines, revisionChanges } from '../report'
import { Rows, useRun, type PageProps } from '../ui'

const verdicts: Record<string, string> = { correct: 'right', incorrect: 'not right', unclear: 'unclear' }
const helpNames: Record<string, string> = { light_prompt: 'a small hint', scaffold: 'a frame', full_model: 'a full model' }
type Attempt = { task_id: string, judgment: string, inputs?: { text?: string }[], help?: Record<string, string>, pronunciation?: { status?: string } | null }

const storyOf = (fact: Doc, objects: Doc[]) => String((objects.find(doc => doc.kind === 'course' && doc.object_id === (fact.body.course_ref as Doc).object_id)
  ?.body.parent_preview as Record<string, unknown> | undefined)?.story ?? 'Class')

function attemptRows(fact: Doc): [string, string][] {
  return ((fact.body.attempts ?? []) as Attempt[]).map(attempt => {
    const said = (attempt.inputs ?? []).map(input => input.text).filter(Boolean).join(' / ') || (attempt.pronunciation ? 'reading aloud' : 'none')
    const help = Object.values(attempt.help ?? {}).map(level => helpNames[level] ?? level).join(', ')
    return [attempt.task_id, `Your answer: ${said}; in class: ${verdicts[attempt.judgment] ?? attempt.judgment}${help ? `; help: ${help}` : ''}`]
  })
}

function Detail({ token, dashboard, learner, go, refresh, fact }: PageProps & { fact: Doc }) {
  const { busy, run, setMessage, status } = useRun(refresh)
  const [note, setNote] = useState('')
  const objects = dashboard.objects ?? []
  const activity = String(fact.body.activity_id)
  const row = evaluationRows(objects).find(item => item.activity_id === activity)
  const evaluation = objects.find(doc => doc.kind === 'evaluation' && doc.body.activity_id === activity)
  const previous = (dashboard.previous_evaluations ?? []).find(doc => doc.object_id === evaluation?.object_id)
  const notes = objects.filter(doc => doc.kind === 'feedback' && doc.body.activity_id === activity)
    .sort((a, b) => a.source_revision - b.source_revision)
  const send = (objection: boolean, label = objection ? 'Sending your objection' : 'Saving your note') => { const body = { request_id: uid(), learner_id: learner, activity_id: activity, text: note.trim(), objection }
    void run(async () => { await api(token, 'evaluation/feedback', body); setNote(''); setMessage('Saved. Jane will evaluate again with it.') }, label) }
  return <>
    <button className="quiet" onClick={() => go('evaluations')}>← All evaluations</button>
    <section><h2>{storyOf(fact, objects)}</h2>
      <Rows rows={[['Status', row?.state], ['Ended', endLabels[String((fact.body.end as Record<string, unknown>)?.reason)] ?? 'Unknown']]} />
      <h3>Your answers and help</h3><Rows rows={attemptRows(fact)} />
      {row?.state !== 'Evaluating' && evaluation ? <><h3>Evaluation</h3><Rows rows={evaluationLines(evaluation, objects)} /></> : <p>The evaluation is being written; this page updates by itself.</p>}
      {row?.state !== 'Evaluating' && evaluation && previous && <><h3>Compared with the last version (version {previous.revision} → version {evaluation.revision})</h3>
        <Rows rows={revisionChanges(evaluation, previous, objects)} />
        {row?.state !== 'Acknowledged' && <p>Your progress updates from this version after you acknowledge it.</p>}</>}
      {notes.length > 0 && <><h3>Your notes</h3><Rows rows={notes.map((doc, index): [string, unknown] =>
        [`Note ${index + 1}${(doc.body.corrections as unknown[] | undefined)?.length ? ' (objection)' : ''}`, doc.body.text])} /></>}</section>
    {status}
    {row && row.state !== 'Acknowledged' && <section><h2>Add a note or object</h2>
      <label>Help that was not recorded, what happened away from the screen, or what you disagree with<textarea value={note} onChange={event => setNote(event.target.value)} /></label>
      <div className="row"><button className="quiet" disabled={busy || !note.trim()} onClick={() => send(false)}>Add a note</button>
        <button className="quiet" disabled={busy || !note.trim()} onClick={() => send(true)}>Object</button>
        {row.evaluation_ref && <button disabled={busy} onClick={() => { const body = { request_id: uid(), learner_id: learner, activity_id: activity, evaluation_ref: row.evaluation_ref }
          void run(async () => { await api(token, 'evaluation/acknowledge', body) }, 'Acknowledging') }}>Acknowledge</button>}</div></section>}
    {row?.state === 'Acknowledged' && evaluation && <section><h2>Correct something</h2>
      <p>Found a mistake? Add a correction. Jane evaluates again with it, and you acknowledge the new version; the old evaluation and record stay.</p>
      <label>Your correction<textarea value={note} onChange={event => setNote(event.target.value)} /></label>
      <button className="quiet" disabled={busy || !note.trim()} onClick={() => send(true, 'Adding your correction')}>Add correction</button></section>}
  </>
}

export default function Evaluations(props: PageProps) {
  const { dashboard, detail, go } = props
  const objects = dashboard.objects ?? []
  const fact = detail ? objects.find(doc => doc.kind === 'fact' && doc.body.activity_id === detail) : undefined
  if (fact) return <Detail {...props} fact={fact} />
  const facts = new Map(objects.filter(doc => doc.kind === 'fact').map(doc => [String(doc.body.activity_id), doc]))
  const rows = evaluationRows(objects)
  return <section><h2>Evaluations</h2>{rows.length ? <ul className="list">{rows.map(row => <li key={row.activity_id}>
    <button className="link" onClick={() => go('evaluations', row.activity_id)}>{storyOf(facts.get(row.activity_id)!, objects)}</button><span className="tag">{row.state}</span></li>)}</ul>
    : <p>No evaluations yet.</p>}</section>
}
