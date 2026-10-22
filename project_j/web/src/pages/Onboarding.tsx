import { useEffect, useRef, useState } from 'react'
import { api, uid } from '../api'
import { decodePcm16, encodeWav } from '../audio'
import type { Document as Doc, JobRow, RevisionRef as Ref } from '../client/types.gen'
import { chatComplete, lexileError } from '../onboarding'
import { evaluationLines } from '../report'
import { JobLine, Progress, Rows, useRun, type PageProps } from '../ui'

// Jane asks each question aloud first; the whole sentence is played as one file.
async function askAloud(token: string, index: number, player: { current?: HTMLAudioElement }, onPlaying: () => void) {
  const response = await fetch(`/api/onboarding/speech/${index}`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' })
  if (!response.ok) throw new Error('Jane\'s voice did not come')
  const rate = Number(/rate=(\d+)/.exec(response.headers.get('content-type') ?? '')?.[1] ?? 24000)
  const samples = decodePcm16(new Uint8Array(await response.arrayBuffer()), null).samples
  const url = URL.createObjectURL(new Blob([encodeWav(samples, rate)], { type: 'audio/wav' }))
  player.current?.pause()
  const element = new Audio(url)
  player.current = element
  element.onplaying = onPlaying
  await new Promise<void>((done, fail) => {
    element.onended = () => done()
    element.onerror = () => fail(new Error('It could not play'))
    element.play().catch(fail)
  }).finally(() => URL.revokeObjectURL(url))
}

function Lexile({ token, learner, refresh }: Pick<PageProps, 'token' | 'learner' | 'refresh'>) {
  const { busy, run, status } = useRun(refresh)
  const [value, setValue] = useState('')
  const [touched, setTouched] = useState(false)
  const error = lexileError(value)
  return <section><h2>Your Lexile</h2>
    <p>Your Lexile sets how hard the stories are, like 650L. Below 0, write it like BR100L. Not sure? Enter a guess and change it later on the Progress page.</p>
    <label>Lexile<input value={value} onChange={event => setValue(event.target.value)} onBlur={() => setTouched(true)} /></label>
    {touched && error && <p className="notice">{error}</p>}
    {status}
    <button disabled={busy || error !== null} onClick={() => { const requestId = uid(); void run(async () => {
      await api(token, 'onboarding/lexile', { request_id: requestId, learner_id: learner, lexile: value })
    }, 'Saving') }}>Save and go on</button></section>
}

function Chat({ token, learner, questions, voiced, refresh }: Pick<PageProps, 'token' | 'learner' | 'refresh'> & { questions: string[], voiced: boolean }) {
  const { busy, run, setMessage, status } = useRun(refresh)
  const [answers, setAnswers] = useState<string[]>([])
  const [index, setIndex] = useState(0)
  const [heard, setHeard] = useState(!voiced)
  // While Jane's question is fetched or played the button is gone: a second press used to queue it again.
  const [asking, setAsking] = useState<'' | 'preparing' | 'speaking'>('')
  const player = useRef<{ current?: HTMLAudioElement }>({})
  useEffect(() => () => player.current.current?.pause(), [])
  const listen = () => {
    setAsking('preparing')
    void askAloud(token, index, player.current, () => setAsking('speaking'))
      .catch(error => setMessage(`${(error as Error).message}. Read the question and answer it.`)).finally(() => { setAsking(''); setHeard(true) })
  }
  const last = index === questions.length - 1
  return <section><h2>Chat with Jane</h2><p>Question {index + 1} of {questions.length}. Write your answer in any language.</p>
    {!heard && (asking === 'preparing' ? <Progress label="Jane is getting the question ready…" />
      : asking === 'speaking' ? <p className="notice" role="status">Jane is asking…</p>
      : <button onClick={listen}>Hear Jane ask</button>)}
    {heard && <><p className="question">{questions[index]}</p>
      <textarea aria-label="Your answer" value={answers[index] ?? ''} onChange={event => setAnswers(current => { const next = [...current]; next[index] = event.target.value; return next })} /></>}
    {status}
    <div className="row">
      {index > 0 && <button className="quiet" disabled={busy} onClick={() => { setIndex(index - 1); setHeard(true) }}>Back</button>}
      {!last && <button disabled={!answers[index]?.trim()} onClick={() => { setIndex(index + 1); setHeard(!voiced) }}>Next</button>}
      {last && <button disabled={busy || !chatComplete(answers, questions.length)} onClick={() => { const requestId = uid(); void run(async () => {
        await api(token, 'onboarding/chat', { request_id: requestId, learner_id: learner, answers })
      }, 'Saving the chat') }}>Submit</button>}</div></section>
}

function Placement({ token, learner, refresh, course, starting }: Pick<PageProps, 'token' | 'learner' | 'refresh'> & { course: Ref, starting?: JobRow }) {
  const { busy, run, status } = useRun(refresh)
  return <section><h2>Your first lesson</h2>
    <p>A short lesson of about 30 minutes: read some English, answer a few questions and read a part aloud. It only shows where you start.</p>
    {starting && <JobLine job={starting} />}
    {starting?.state !== 'running' && <>{status}
      <button disabled={busy} onClick={() => { const requestId = uid(); void run(async () => {
        await api(token, 'course/start', { request_id: requestId, learner_id: learner, course_ref: course, materials_ready: false })
      }, 'Starting') }}>Start</button></>}</section>
}

const fields = [['background', 'Why you learn and how you use English'], ['interests', 'Interests'], ['weekly_time', 'Time each week'], ['focus', 'What to get better at']] as const

/** The profile as editable fields; onboarding confirms it, the state page edits it later. */
export function ProfileForm({ token, learner, refresh, profile, evaluation, objects, title, intro, action }: Pick<PageProps, 'token' | 'learner' | 'refresh'>
  & { profile: Doc, evaluation?: Doc, objects: Doc[], title: string, intro: string, action: string }) {
  const { busy, run, setMessage, status } = useRun(refresh)
  const body = profile.body as Record<string, string | null>
  const keys = ['lexile', ...fields.map(([key]) => key), 'materials']
  const [draft, setDraft] = useState<Record<string, string>>(() => Object.fromEntries(keys.map(key => [key, body[key] ?? ''])))
  const edit = (key: string) => (event: { target: { value: string } }) => setDraft(current => ({ ...current, [key]: event.target.value }))
  const lexile = lexileError(draft.lexile)
  return <section><h2>{title}</h2><p>{intro}</p>
    <label>Lexile<input value={draft.lexile} onChange={edit('lexile')} /></label>
    {lexile && <p className="notice">{lexile}</p>}
    {fields.map(([key, label]) => <label key={key}>{label}<textarea value={draft[key]} onChange={edit(key)} /></label>)}
    <label>Things you have at hand (optional; without them, lessons have no tasks away from the screen)<textarea value={draft.materials} onChange={edit('materials')} /></label>
    {evaluation && <><h3>First lesson evaluation</h3><Rows rows={evaluationLines(evaluation, objects)} /><p>Confirming your profile also acknowledges this evaluation.</p></>}
    {status}
    <button disabled={busy || lexile !== null || fields.some(([key]) => !draft[key].trim())} onClick={() => { const requestId = uid(); void run(async () => {
      await api(token, 'profile', { request_id: requestId, learner_id: learner, ...draft, materials: draft.materials.trim() || null })
      setMessage('Your profile is saved.')
    }, 'Saving your profile') }}>{action}</button></section>
}

export default function Onboarding({ token, dashboard, learner, refresh }: PageProps) {
  const next = dashboard.next ?? { kind: 'waiting' }
  const subject = (next.subject ?? {}) as Record<string, any>
  const objects = dashboard.objects ?? []
  const jobs = dashboard.jobs ?? []
  const steps = ['lexile', 'chat', 'placement', 'profile']
  const props = { token, learner, refresh }
  const head = <header><h1>Getting started</h1><p>Enter your Lexile, chat with Jane, take a first lesson, then check the profile Jane writes.</p>
    <ol className="steps">{['Lexile', 'Chat', 'First lesson', 'Profile'].map((label, index) =>
      <li key={label} className={steps.indexOf(next.kind) === index ? 'current' : ''}>{label}</li>)}</ol></header>
  if (next.kind === 'lexile') return <>{head}<Lexile {...props} /></>
  if (next.kind === 'chat') return <>{head}<Chat {...props} questions={subject.questions ?? []} voiced={dashboard.realm === 'study'} /></>
  if (next.kind === 'placement') {
    const starting = jobs.find(job => job.name.startsWith('start:'))
    return <>{head}<Placement {...props} course={subject.course_ref} starting={starting} /></>
  }
  if (next.kind === 'profile') {
    const profile = objects.find(doc => doc.kind === 'profile')
    const evaluation = objects.find(doc => doc.kind === 'evaluation' && doc.body.activity_id === subject.activity_id)
    return <>{head}{profile && <ProfileForm {...props} profile={profile} evaluation={evaluation} objects={objects} title="Check your profile"
      intro="Jane wrote this from your chat and first lesson. Lessons are chosen and made from it. Change anything, then confirm." action="Confirm" />}</>
  }
  return <>{head}<Waiting {...props} jobs={jobs} failed={jobs.filter(job => job.state === 'failed').map(job => job.name)} /></>
}

function Waiting({ token, learner, refresh, jobs, failed }: Pick<PageProps, 'token' | 'learner' | 'refresh'> & { jobs: JobRow[], failed: string[] }) {
  const { busy, run, status } = useRun(refresh)
  return <section><h2>Jane is getting ready</h2>{jobs.length ? jobs.map(job => <JobLine key={job.name} job={job} />) : <Progress label="Please wait; this page updates by itself." />}
    {status}
    {failed.map(name => <button key={name} className="quiet" disabled={busy} onClick={() => void run(async () => {
      await api(token, 'jobs/retry', { learner_id: learner, name })
    }, 'Trying again')}>Try again</button>)}</section>
}
