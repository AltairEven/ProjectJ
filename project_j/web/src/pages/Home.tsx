import { useState } from 'react'
import { api, uid } from '../api'
import type { Document as Doc } from '../client/types.gen'
import { dateText } from '../dates'
import { byNewest, CHALLENGES, challengeOption } from '../learning'
import { chosenTopic, focusError, initialFocus, nextStepView } from '../next-step'
import { endLabels } from '../report'
import { JobLine, Rows, useRun, type PageProps } from '../ui'

type Skill = { skill_id: string, group: string, label: string }
type Suggestion = { skill_id: string, reason: string }
type Topic = { title: string, reason: string, form_label?: string | null }

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
  const [challenge, setChallenge] = useState<string>(subject.challenge ?? 'challenge')
  const targets: Record<string, string> = subject.targets ?? {}
  const topic = chosenTopic(choice, topics, own)
  const error = focusError(picked.length, note, topic)
  const reasons = new Map(suggestions.map(item => [item.skill_id, item.reason]))
  const toggle = (id: string, on: boolean) => setPicked(current => on ? [...current, id] : current.filter(item => item !== id))
  const send = () => {
    const body = { request_id: uid(), learner_id: learner, demand_ref: subject.demand_ref, topic, skills: picked, note: note.trim() || null, challenge }
    void run(async () => { await api(token, 'focus', body) }, 'Saving your choice')
  }
  return <>
    <p>{topics.length ? 'Jane found some topics from your interests, each as a different kind of text, and listed things you can practise. ' : 'Write a topic for this lesson, then tick what you want to practise. '}When you send it, Jane starts making the lesson.</p>
    <fieldset className="skills"><legend>{topics.length ? 'Topic (choose one)' : 'Topic'}</legend>
      {topics.map((item, index) => <label key={index} className="check">
        <input type="radio" name="topic" checked={choice === index} onChange={() => setChoice(index)} />
        {item.form_label && <span className="tag">{item.form_label}</span>}{item.title}<span className="tag">{item.reason}</span></label>)}
      {topics.length > 0 && <label className="check"><input type="radio" name="topic" checked={choice === 'own'} onChange={() => setChoice('own')} />Something else:</label>}
      <input aria-label="My own topic" placeholder="Write what you want to read about" value={own} onFocus={() => setChoice('own')} onChange={event => setOwn(event.target.value)} /></fieldset>
    <fieldset className="skills"><legend>Story difficulty</legend>
      {CHALLENGES.map(([name]) => <label key={name} className="check">
        <input type="radio" name="challenge" checked={challenge === name} onChange={() => setChallenge(name)} />
        {challengeOption(name, targets[name])}</label>)}</fieldset>
    <p className="hint">Things to practise (up to 3, or none)</p>
    {[...new Set(options.map(item => item.group))].map(group => <fieldset key={group} className="skills"><legend>{group}</legend>
      {options.filter(item => item.group === group).map(item => <label key={item.skill_id} className="check">
        <input type="checkbox" checked={picked.includes(item.skill_id)} onChange={event => toggle(item.skill_id, event.target.checked)} />
        {item.label}{reasons.has(item.skill_id) && <span className="tag">Jane suggests: {reasons.get(item.skill_id)}</span>}</label>)}</fieldset>)}
    <label>Something else to practise (optional)<input value={note} onChange={event => setNote(event.target.value)} /></label>
    {error && <p className={topic ? 'notice' : 'hint'}>{error}</p>}
    {status}
    <div className="row"><button disabled={busy || error !== null} onClick={send}>Send to Jane</button></div>
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
    <section className="next"><h2>Next</h2><p className="next-title">{view.title}</p>
      {(view.action || view.also) && <div className="row">
        {view.action && <button onClick={() => go(view.page, view.detail)}>{view.action}</button>}
        {view.also && <button className="quiet" onClick={() => go(view.also!.page, view.also!.detail)}>{view.also.action}</button>}</div>}
      {dashboard.next?.kind === 'focus' && <FocusPicker token={token} learner={learner} refresh={refresh} subject={dashboard.next.subject ?? {}} />}</section>
    {status}
    <section><h2>Working now</h2>
      {jobs.length ? <ul className="list jobs">{jobs.map(job => <li key={job.name}><JobLine job={job} />
        {/* A failed confirm or start is retried from its course page. */}
        {job.state === 'failed' && !job.name.startsWith('start:') && !job.name.startsWith('confirm:') &&
          <button className="quiet" disabled={busy} onClick={() => void retry(job.name)}>Try again</button>}</li>)}</ul>
        : <p>Nothing is running.</p>}</section>
    <section><h2>Last lesson</h2>{fact ? <>
      <Rows rows={[
        ['Lesson', (course?.body.parent_preview as Record<string, string> | undefined)?.story],
        ['Ended', endLabels[String((fact.body.end as Record<string, unknown>)?.reason)] ?? 'Unknown'],
        ['Evaluation', (evaluation?.body.report as Record<string, string> | undefined)?.summary ?? 'Being written'],
      ]} />
      <button className="quiet" onClick={() => go('evaluations', activity)}>Read the evaluation</button></> : <p>No lessons yet.</p>}</section>
    <section><h2>This week</h2>{week ? <Rows rows={[
      ['Main goal', (week.body.main_goal as Record<string, string>).description],
      ['Next review', week.body.review_at ? dateText(String(week.body.review_at)) : 'Not set'],
    ]} /> : <p>No plan yet.</p>}</section>
  </>
}
