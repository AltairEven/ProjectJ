import { useRef, useState } from 'react'
import { api, fileUrl, show, uid } from '../api'
import type { Document as Doc } from '../client/types.gen'
import { courseRows, latestFact, lessonOf, needsMaterials, previewLabels, previewOf, previewRank, resourcesOf, retakeOffered, storyLevelLine } from '../courses'
import { discussionLines, resourceLines } from '../report'
import { JobLine, Rows, useRun, type PageProps } from '../ui'

// Resource kinds with a connected generator: voice by local synthesis, the rest by the model.
const generatable = ['audio', 'image', 'animation']
type Resource = { resource_id: string, kind: string, purpose: string, versions?: unknown[], adopted_version?: number | null }

function Detail({ token, dashboard, learner, go, refresh, course }: PageProps & { course: Doc }) {
  const { busy, run, status } = useRun(refresh)
  const objects = dashboard.objects ?? []
  const [feedback, setFeedback] = useState('')
  const [ready, setReady] = useState(false)
  const [shown, setShown] = useState<Record<string, string>>({})
  const [picked, setPicked] = useState<Record<string, number>>({})
  const listening = useRef<HTMLAudioElement | null>(null)
  const state = courseRows(objects).find(row => row.object_id === course.object_id)?.state
  const preview = previewOf(course, objects)
  const content = (preview?.body.content ?? course.body.parent_preview) as Record<string, unknown>
  const confirmed = state !== 'Being made' && state !== 'Waiting for you'
  const taught = state === 'Taken' || state === 'Evaluated'
  const retaking = taught && retakeOffered(dashboard.next, course)  // Stopped: the same class from the start.
  const courseRef = { object_id: course.object_id, revision: course.revision }
  // Each resource is its own record; once confirmed, the lesson folder holds the one in use (ADR-0027).
  const resources = resourcesOf(course, objects).map(doc => doc.body) as Resource[]
  const lesson = lessonOf(course, objects)
  const job = (kind: string) => (dashboard.jobs ?? []).find(item => item.name === `${kind}:${course.object_id}:${course.revision}`)
  const confirming = job('confirm'), starting = job('start'), remaking = job('remake')
  const materials = needsMaterials(course)
  const fact = latestFact(course, objects)
  // Request bodies are made once, so resuming a failed operation repeats the same request.
  const generate = (item: Resource) => {
    const body = { request_id: uid(), learner_id: learner, course_ref: courseRef, resource_id: item.resource_id }
    return async () => {
      await api(token, 'resource/generate', body)
      setPicked(current => { const rest = { ...current }; delete rest[item.resource_id]; return rest })
    }
  }
  const adopt = (item: Resource, version: number) => {
    const body = { request_id: uid(), learner_id: learner, course_ref: courseRef, resource_id: item.resource_id, version }
    return async () => { await api(token, 'resource/adopt', body) }
  }
  const whole = () => ({ request_id: uid(), learner_id: learner, course_ref: courseRef })
  const look = (item: Resource, version: number) => async () => {
    const query = new URLSearchParams({ learner_id: learner, object_id: course.object_id, revision: String(course.revision),
      resource_id: item.resource_id, version: String(version) })
    const url = await fileUrl(token, `/api/resource/file?${query}`)
    if (item.kind === 'audio') { listening.current?.pause(); listening.current = new Audio(url); await listening.current.play() }
    else setShown(current => ({ ...current, [item.resource_id]: url }))
  }
  return <>
    <button className="quiet" onClick={() => go('courses')}>← All lessons</button>
    <section className="preview"><h2>{String(content?.story ?? 'Lesson details')}</h2><p>Status: {state} (version {course.revision})</p>
      {content ? <Rows rows={Object.entries(content).sort(([a], [b]) => previewRank(a) - previewRank(b)).map(([key, value]) => [previewLabels[key] ?? key, show(value)])} />
        : <p>The details are not here yet.</p>}
      {storyLevelLine(course) && <Rows rows={[['Story level', storyLevelLine(course)]]} />}
      {((course.body.vocabulary_used as string[] | undefined) ?? []).length > 0 && <Rows rows={[['Review words', (course.body.vocabulary_used as string[]).join(', ')]]} />}
      {preview?.body.note ? <p>Note: {String(preview.body.note)}</p> : null}
      {preview && <Rows rows={discussionLines(preview.body)} />}</section>
    {status}
    {!confirmed && <section><h2>Ask for changes</h2>
      <label>Write what to change, and Jane makes a new version (goals and the 30 minutes are changed on the Plan page)<textarea value={feedback} onChange={event => setFeedback(event.target.value)} /></label>
      <button className="quiet" disabled={busy || !feedback.trim()} onClick={() => { const body = { request_id: uid(), learner_id: learner, course_ref: courseRef, feedback: feedback.trim() }
        void run(async () => { await api(token, 'course/discuss', body); setFeedback('') }, 'Changing the lesson') }}>Send</button></section>}
    {!confirmed && <section><h2>Confirm the lesson</h2>
      <p>Confirming saves this version, and Jane makes its voice, pictures and other resources. The class does not start by itself; you start it when you are ready.</p>
      <button disabled={busy} onClick={() => { const body = { request_id: uid(), learner_id: learner, course_ref: courseRef }
        void run(async () => { await api(token, 'course/confirm', body) }, 'Confirming') }}>Confirm</button></section>}
    {confirmed && (!taught || retaking) && <section><h2>{retaking ? 'Take this lesson again' : 'Start the class'}</h2>
      {(starting ?? confirming) && <JobLine job={(starting ?? confirming)!} />}
      {materials && <label className="check"><input type="checkbox" checked={ready} onChange={event => setReady(event.target.checked)} />
        Things are ready{content?.materials ? ` (${show(content.materials)})` : ''}</label>}
      {retaking && <p>Last time it stopped early. That record and its evaluation stay; this time it starts from the beginning, with the voice and pictures already made.</p>}
      <p>{confirming?.state === 'running' ? 'You can start when the resources are ready.' : 'Start when you are ready: about 30 minutes, and you can pause or stop at any time.'}</p>
      <button disabled={busy || confirming?.state === 'running' || starting?.state === 'running' || (materials && !ready)} onClick={() => {
        const body = { request_id: uid(), learner_id: learner, course_ref: courseRef, materials_ready: materials && ready }
        void run(async () => { await api(token, 'course/start', body) }, 'Starting') }}>{starting?.state === 'failed' ? 'Try again' : retaking ? 'Take it again' : 'Start'}</button></section>}
    {taught && fact && <section><h2>You took this lesson</h2><button className="quiet" onClick={() => go('evaluations', String(fact.body.activity_id))}>Read the evaluation</button></section>}
    {confirmed && preview && <section><h2>Resources</h2>
      {lesson && <p>Confirmed. The lesson folder is {String(lesson.body.folder)}.</p>}
      {remaking && <JobLine job={remaking} />}
      {resources.length ? <ul>{resourceLines(resources, !!lesson).map((line, index) => {
        const item = resources[index], latest = item.versions?.length ?? 0
        const version = lesson ? item.adopted_version ?? 0 : Math.min(picked[item.resource_id] ?? latest, latest)
        return <li key={item.resource_id}>{line}
          {generatable.includes(item.kind) && (lesson ? version > 0 && <div className="row">
            <button className="quiet" disabled={busy} onClick={() => void run(look(item, version))}>{item.kind === 'audio' ? 'Listen' : 'Look'}</button></div>
          : <div className="row">
            <button className="quiet" disabled={busy} onClick={() => void run(generate(item), `Making ${item.resource_id}`)}>{latest ? 'Make again' : 'Make'}</button>
            {latest > 1 && <select aria-label="Choose a version" value={version} disabled={busy}
              onChange={event => setPicked(current => ({ ...current, [item.resource_id]: Number(event.target.value) }))}>
              {Array.from({ length: latest }, (_, i) => i + 1).map(n => <option key={n} value={n}>Version {n}{item.adopted_version === n ? ' (in use)' : ''}</option>)}</select>}
            {latest > 0 && <><button className="quiet" disabled={busy} onClick={() => void run(look(item, version))}>{item.kind === 'audio' ? 'Listen to' : 'Look at'} version {version}</button>
              <button disabled={busy || item.adopted_version === version} onClick={() => void run(adopt(item, version))}>Use version {version}</button></>}
          </div>)}
          {shown[item.resource_id] && <img className="resource-preview" src={shown[item.resource_id]} alt={item.purpose} />}
        </li>
      })}</ul> : <p>This lesson needs no extra resources.</p>}
      {lesson ? <><p>If a voice or picture no longer plays or shows, Jane makes only that one again.</p>
        <button className="quiet" disabled={busy || remaking?.state === 'running'} onClick={() => void run(async () => { await api(token, 'course/resources/remake', whole()) })}>Remake broken resources</button></>
      : <><h3>Confirm resources</h3>
        <p>Confirming keeps the version in use for each resource and removes the others. After that they cannot be changed.</p>
        <button disabled={busy || confirming?.state === 'running'} onClick={() => void run(async () => { await api(token, 'course/resources/confirm', whole()) }, 'Confirming resources')}>Confirm resources</button></>}</section>}
  </>
}

export default function Courses(props: PageProps) {
  const { dashboard, detail, go } = props
  const objects = dashboard.objects ?? []
  const course = detail ? objects.find(doc => doc.kind === 'course' && doc.object_id === detail) : undefined
  if (course) return <Detail {...props} course={course} />
  const preparing = (dashboard.jobs ?? []).filter(job => job.name.startsWith('prepare:'))
  const rows = courseRows(objects)
  return <section><h2>Lessons</h2>
    {preparing.map(job => <JobLine key={job.name} job={job} />)}
    {rows.length ? <ul className="list">{rows.map(row => <li key={row.object_id}>
      <button className="link" onClick={() => go('courses', row.object_id)}>{row.title}</button><span className="tag">{row.state}</span></li>)}</ul>
      : <p>No lessons yet.</p>}</section>
}
