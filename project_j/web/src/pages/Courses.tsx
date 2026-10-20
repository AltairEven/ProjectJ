import { useRef, useState } from 'react'
import { api, fileUrl, show, uid } from '../api'
import type { Document as Doc } from '../client/types.gen'
import { courseRows, needsMaterials, previewLabels, previewOf, previewRank } from '../courses'
import { jobLines } from '../next-step'
import { discussionLines, resourceLines } from '../report'
import { Rows, useRun, type PageProps } from '../ui'

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
  const confirmed = state !== '备课中' && state !== '等你确认'
  const taught = state === '已上' || state === '已评价'
  const courseRef = { object_id: course.object_id, revision: course.revision }
  const resources = (preview?.body.resources ?? []) as Resource[]
  const starting = (dashboard.jobs ?? []).find(job => job.name === `start:${course.object_id}:${course.revision}`)
  const materials = needsMaterials(course)
  const fact = objects.find(doc => doc.kind === 'fact' && (doc.body.course_ref as Doc | undefined)?.object_id === course.object_id)
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
  const look = (item: Resource, version: number) => async () => {
    const query = new URLSearchParams({ learner_id: learner, object_id: course.object_id, revision: String(course.revision),
      resource_id: item.resource_id, version: String(version) })
    const url = await fileUrl(token, `/api/resource/file?${query}`)
    if (item.kind === 'audio') { listening.current?.pause(); listening.current = new Audio(url); await listening.current.play() }
    else setShown(current => ({ ...current, [item.resource_id]: url }))
  }
  return <>
    <button className="quiet" onClick={() => go('courses')}>← 全部课程</button>
    <section className="preview"><h2>{String(content?.story ?? '课程详情')}</h2><p>状态：{state}（修订{course.revision}）</p>
      {content ? <Rows rows={Object.entries(content).sort(([a], [b]) => previewRank(a) - previewRank(b)).map(([key, value]) => [previewLabels[key] ?? key, show(value)])} />
        : <p>课程详情待取得。</p>}
      {preview?.body.note ? <p>说明：{String(preview.body.note)}</p> : null}
      {preview && <Rows rows={discussionLines(preview.body)} />}</section>
    {status}
    {!confirmed && <section><h2>修改意见</h2>
      <label>想改哪里就写哪里，Jane 会改出新版本（改目标或30分钟预算要回到学习计划）<textarea value={feedback} onChange={event => setFeedback(event.target.value)} /></label>
      <button className="quiet" disabled={busy || !feedback.trim()} onClick={() => { const body = { request_id: uid(), learner_id: learner, course_ref: courseRef, feedback: feedback.trim() }
        void run(async () => { await api(token, 'course/discuss', body); setFeedback('') }, '按修改意见修订课程') }}>提交修改意见</button></section>}
    {!taught && <section><h2>开始上课</h2>
      {starting && <p className="notice" role="status">{jobLines([starting])[0]}</p>}
      {materials && <label className="check"><input type="checkbox" checked={ready} onChange={event => setReady(event.target.checked)} />
        实物已按本课安排备好{content?.materials ? `（${show(content.materials)}）` : ''}</label>}
      <p>确认后 Jane 会生成本课的语音、图片等资源，完成后直接进入课堂。</p>
      <button disabled={busy || starting?.state === 'running' || (materials && !ready)} onClick={() => {
        const body = { request_id: uid(), learner_id: learner, course_ref: courseRef, materials_ready: materials && ready }
        void run(async () => { await api(token, 'course/start', body) }, '确认课程并准备资源') }}>{starting?.state === 'failed' ? '再试一次' : '确认并开始上课'}</button></section>}
    {taught && fact && <section><h2>这节课已上</h2><button className="quiet" onClick={() => go('evaluations', String(fact.body.activity_id))}>查看课后评价</button></section>}
    {confirmed && preview && <section><h2>教学资源</h2>
      {resources.length ? <ul>{resourceLines(preview.body).map((line, index) => {
        const item = resources[index], latest = item.versions?.length ?? 0
        const version = Math.min(picked[item.resource_id] ?? latest, latest)
        return <li key={item.resource_id}>{line}
          {generatable.includes(item.kind) && <div className="row">
            <button className="quiet" disabled={busy} onClick={() => void run(generate(item), `生成 ${item.resource_id}`)}>{latest ? '重新生成' : '生成'}</button>
            {latest > 1 && <select aria-label="选择版本" value={version} disabled={busy}
              onChange={event => setPicked(current => ({ ...current, [item.resource_id]: Number(event.target.value) }))}>
              {Array.from({ length: latest }, (_, i) => i + 1).map(n => <option key={n} value={n}>第{n}版{item.adopted_version === n ? '（已采用）' : ''}</option>)}</select>}
            {latest > 0 && <><button className="quiet" disabled={busy} onClick={() => void run(look(item, version))}>{item.kind === 'audio' ? '试听' : '查看'}第{version}版</button>
              <button disabled={busy || item.adopted_version === version} onClick={() => void run(adopt(item, version))}>采用第{version}版</button></>}
          </div>}
          {shown[item.resource_id] && <img className="resource-preview" src={shown[item.resource_id]} alt={item.purpose} />}
        </li>
      })}</ul> : <p>本课不需要额外资源。</p>}</section>}
  </>
}

export default function Courses(props: PageProps) {
  const { dashboard, detail, go } = props
  const objects = dashboard.objects ?? []
  const course = detail ? objects.find(doc => doc.kind === 'course' && doc.object_id === detail) : undefined
  if (course) return <Detail {...props} course={course} />
  const preparing = (dashboard.jobs ?? []).filter(job => job.name.startsWith('prepare:'))
  const rows = courseRows(objects)
  return <section><h2>课程</h2>
    {jobLines(preparing).map(line => <p key={line} className="notice">{line}</p>)}
    {rows.length ? <ul className="list">{rows.map(row => <li key={row.object_id}>
      <button className="link" onClick={() => go('courses', row.object_id)}>{row.title}</button><span className="tag">{row.state}</span></li>)}</ul>
      : <p>还没有课程。</p>}</section>
}
