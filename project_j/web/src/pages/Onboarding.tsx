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
  if (!response.ok) throw new Error('Jane 的声音未取得')
  const rate = Number(/rate=(\d+)/.exec(response.headers.get('content-type') ?? '')?.[1] ?? 24000)
  const samples = decodePcm16(new Uint8Array(await response.arrayBuffer()), null).samples
  const url = URL.createObjectURL(new Blob([encodeWav(samples, rate)], { type: 'audio/wav' }))
  player.current?.pause()
  const element = new Audio(url)
  player.current = element
  element.onplaying = onPlaying
  await new Promise<void>((done, fail) => {
    element.onended = () => done()
    element.onerror = () => fail(new Error('播放失败'))
    element.play().catch(fail)
  }).finally(() => URL.revokeObjectURL(url))
}

function Lexile({ token, learner, refresh }: Pick<PageProps, 'token' | 'learner' | 'refresh'>) {
  const { busy, run, status } = useRun(refresh)
  const [value, setValue] = useState('')
  const [touched, setTouched] = useState(false)
  const error = lexileError(value)
  return <section><h2>你的蓝思值</h2>
    <p>蓝思值（Lexile）是选故事难度的起点，例如 650L；低于0的写成 BR100L 这样。不确定时可先填一个估计值，之后在学习状态里修改。</p>
    <label>蓝思值<input value={value} onChange={event => setValue(event.target.value)} onBlur={() => setTouched(true)} /></label>
    {touched && error && <p className="notice">{error}</p>}
    {status}
    <button disabled={busy || error !== null} onClick={() => { const requestId = uid(); void run(async () => {
      await api(token, 'onboarding/lexile', { request_id: requestId, learner_id: learner, lexile: value })
    }, '保存') }}>保存，下一步</button></section>
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
      .catch(error => setMessage(`${(error as Error).message}，先看文字回答。`)).finally(() => { setAsking(''); setHeard(true) })
  }
  const last = index === questions.length - 1
  return <section><h2>和 Jane 聊几句</h2><p>第{index + 1}/{questions.length}题。用文字回答，中文或英文都可以。</p>
    {!heard && (asking === 'preparing' ? <Progress label="Jane 正在准备问题……" />
      : asking === 'speaking' ? <p className="notice" role="status">Jane 正在问……</p>
      : <button onClick={listen}>听 Jane 问</button>)}
    {heard && <><p className="question">{questions[index]}</p>
      <textarea aria-label="你的回答" value={answers[index] ?? ''} onChange={event => setAnswers(current => { const next = [...current]; next[index] = event.target.value; return next })} /></>}
    {status}
    <div className="row">
      {index > 0 && <button className="quiet" disabled={busy} onClick={() => { setIndex(index - 1); setHeard(true) }}>上一题</button>}
      {!last && <button disabled={!answers[index]?.trim()} onClick={() => { setIndex(index + 1); setHeard(!voiced) }}>下一题</button>}
      {last && <button disabled={busy || !chatComplete(answers, questions.length)} onClick={() => { const requestId = uid(); void run(async () => {
        await api(token, 'onboarding/chat', { request_id: requestId, learner_id: learner, answers })
      }, '保存聊天') }}>提交</button>}</div></section>
}

function Placement({ token, learner, refresh, course, starting }: Pick<PageProps, 'token' | 'learner' | 'refresh'> & { course: Ref, starting?: JobRow }) {
  const { busy, run, status } = useRun(refresh)
  return <section><h2>建档小测</h2>
    <p>一节约30分钟的小课：读一段英文、回答几个问题，再朗读一段。结果只用来了解你现在的起点。</p>
    {starting && <JobLine job={starting} />}
    {starting?.state !== 'running' && <>{status}
      <button disabled={busy} onClick={() => { const requestId = uid(); void run(async () => {
        await api(token, 'course/start', { request_id: requestId, learner_id: learner, course_ref: course, materials_ready: false })
      }, '开始小测') }}>开始小测</button></>}</section>
}

const fields = [['background', '学习目的与现状'], ['interests', '兴趣'], ['weekly_time', '每周可用时间'], ['focus', '想加强的方面']] as const

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
    <label>蓝思值<input value={draft.lexile} onChange={edit('lexile')} /></label>
    {lexile && <p className="notice">{lexile}</p>}
    {fields.map(([key, label]) => <label key={key}>{label}<textarea value={draft[key]} onChange={edit(key)} /></label>)}
    <label>可用实物与条件（可选；没有写时课程不安排离开屏幕的活动）<textarea value={draft.materials} onChange={edit('materials')} /></label>
    {evaluation && <><h3>小测评价</h3><Rows rows={evaluationLines(evaluation, objects)} /><p>确认资料同时表示你认可这份小测评价。</p></>}
    {status}
    <button disabled={busy || lexile !== null || fields.some(([key]) => !draft[key].trim())} onClick={() => { const requestId = uid(); void run(async () => {
      await api(token, 'profile', { request_id: requestId, learner_id: learner, ...draft, materials: draft.materials.trim() || null })
      setMessage('学习资料已保存。')
    }, '保存资料') }}>{action}</button></section>
}

export default function Onboarding({ token, dashboard, learner, refresh }: PageProps) {
  const next = dashboard.next ?? { kind: 'waiting' }
  const subject = (next.subject ?? {}) as Record<string, any>
  const objects = dashboard.objects ?? []
  const jobs = dashboard.jobs ?? []
  const steps = ['lexile', 'chat', 'placement', 'profile']
  const props = { token, learner, refresh }
  const head = <header><h1>建档</h1><p>先填蓝思值，和 Jane 聊几句，上一节小测，再确认 Jane 整理的学习资料。</p>
    <ol className="steps">{['蓝思值', '聊几句', '小测', '确认资料'].map((label, index) =>
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
    return <>{head}{profile && <ProfileForm {...props} profile={profile} evaluation={evaluation} objects={objects} title="确认学习资料"
      intro="Jane 根据聊天和小测整理了下面的资料，之后选课和备课都参考它。可以直接修改，确认后建档完成。" action="确认资料" />}</>
  }
  return <>{head}<Waiting {...props} jobs={jobs} failed={jobs.filter(job => job.state === 'failed').map(job => job.name)} /></>
}

function Waiting({ token, learner, refresh, jobs, failed }: Pick<PageProps, 'token' | 'learner' | 'refresh'> & { jobs: JobRow[], failed: string[] }) {
  const { busy, run, status } = useRun(refresh)
  return <section><h2>Jane 正在准备</h2>{jobs.length ? jobs.map(job => <JobLine key={job.name} job={job} />) : <Progress label="稍等即可，页面会自动更新。" />}
    {status}
    {failed.map(name => <button key={name} className="quiet" disabled={busy} onClick={() => void run(async () => {
      await api(token, 'jobs/retry', { learner_id: learner, name })
    }, '重试')}>重试</button>)}</section>
}
