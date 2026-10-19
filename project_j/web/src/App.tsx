import { useCallback, useEffect, useRef, useState } from 'react'
import { acknowledgePause, cancel, presentationCancelled, type Cancellation } from './cancellation'
import { canConfirm } from './confirmation'
import { describe, discussionLines, endLabels, evaluationLines, missingResources, modelLines, resourceLines, startMessage } from './report'
import { finishRecording, idle, pauseRecording, recordedMs, resumeRecording, startRecording, type Recorder } from './recording'
import { requestFailure, type RequestFailure } from './request'
import { Speaker, reportsFor, startedReport } from './speaker'
import { Microphone } from './microphone'
import { toBase64 } from './audio'
import type { AllowedAction, Snapshot, Dashboard, RevisionRef as Ref, Document as Doc, Control, StudentControl, DeliveryFeedback, TextInput, PreparationResult as PrepareResult, StartResult, StartConditions, InputResult as Ack } from './client/types.gen'

const learner = 'fictional-L1'
const uid = () => crypto.randomUUID()
const show = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2)
const ref = (doc: Doc): Ref => ({ object_id: doc.object_id, revision: doc.revision })
const previewLabels: Record<string, string> = { story: '本课故事', why: '本课目的', materials: '实物准备', help: '帮助方式', exit: '时长与退出' }
const stateLabels: Record<string, string> = { running: '进行中', offscreen: '离屏活动中', paused: '已暂停', closing: '收尾', ended: '已结束' }
type Condition = 'materials' | 'time' | 'accompaniment'
// No default to "available": a required condition must be reported, never assumed (E2E14).
const conditionChoices: [Condition, string, [string, string][]][] = [
  ['materials', '材料', [['unknown', '未确认'], ['reported_available', '实物已按本课安排备好'], ['unavailable', '没有备好']]],
  ['time', '时间', [['unknown', '未确认'], ['reported_30_minutes', '有30分钟'], ['unavailable', '不足30分钟']]],
  ['accompaniment', '陪伴', [['unknown', '未确认'], ['reported_present', '家长可陪伴'], ['unavailable', '家长不能陪伴']]],
]
const previewRank = (key: string) => key in previewLabels ? Object.keys(previewLabels).indexOf(key) : 99
// Resource kinds with a connected generator: voice by local synthesis, the rest by the model.
const generatable = ['audio', 'image', 'animation']
type Resource = { resource_id: string, kind: string, purpose: string, versions?: unknown[], adopted_version?: number | null }

// A generated file fetched with the bearer token, as a URL the page can play or show.
async function fileUrl(token: string, path: string): Promise<string> {
  const response = await fetch(path, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' })
  if (!response.ok) throw requestFailure(response.status, '资源文件未取得')
  return URL.createObjectURL(await response.blob())
}

// Pictures and animations the core approved with an action; shown as images only.
function Visuals({ token, action }: { token: string, action?: AllowedAction }) {
  const [urls, setUrls] = useState<string[]>([])
  const id = action?.action_id, visuals = action?.visuals ?? []
  const key = visuals.join(' ')
  useEffect(() => {
    if (!id || !key) { setUrls([]); return }
    let alive = true
    const made: string[] = []
    void Promise.all(key.split(' ').map(resource => fileUrl(token, `/api/student/visual/${encodeURIComponent(id)}/${encodeURIComponent(resource)}`)
      .then(url => { made.push(url); return url }).catch(() => undefined)))
      .then(list => { if (alive) setUrls(list.filter((url): url is string => Boolean(url))) })
    return () => { alive = false; made.forEach(url => URL.revokeObjectURL(url)) }
  }, [token, id, key])
  return urls.length ? <section className="visuals">{urls.map(url => <img key={url} src={url} alt="Jane 展示的图" />)}</section> : null
}

async function api<T>(token: string, path: string, body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api/${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
    })
  } catch (error) { throw requestFailure(undefined, (error as Error).message) }
  const result = await response.json().catch(() => undefined)
  if (!response.ok || result === undefined) throw requestFailure(response.ok ? undefined : response.status,
    result?.message ?? (result?.detail === undefined ? undefined : show(result.detail)))
  return result as T
}

function Parent({ token }: { token: string }) {
  const [dashboard, setDashboard] = useState<Dashboard>()
  const [prepared, setPrepared] = useState<PrepareResult>()
  const [feedback, setFeedback] = useState('')
  const [courseFeedback, setCourseFeedback] = useState('')
  const [report, setReport] = useState<unknown>()
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [backupId, setBackupId] = useState('')
  const [household, setHousehold] = useState('')
  const [conditions, setConditions] = useState<Pick<StartConditions, Condition>>({ materials: 'unknown', time: 'unknown', accompaniment: 'unknown' })
  const [studentLink, setStudentLink] = useState('')
  const [lostDrafts, setLostDrafts] = useState<Record<string, string>>({})
  const [planGoal, setPlanGoal] = useState('在相近新材料中观察阅读依据，继续保留短英语报告支架。')
  const failedOperation = useRef<(() => Promise<void>) | null>(null)
  const [shown, setShown] = useState<Record<string, string>>({})
  const listening = useRef<HTMLAudioElement | null>(null)
  const refresh = useCallback(async () => {
    const data = await api<Dashboard>(token, `parent/dashboard?learner_id=${learner}`)
    setDashboard(data)
    const courses = (data.objects ?? []).filter(doc => doc.kind === 'course').sort((a, b) => b.created_at.localeCompare(a.created_at))
    const selected = courses[0]
    const matches = (doc: Doc) => selected && doc.dependency_refs?.some(
      dep => dep.object_id === selected.object_id && dep.revision === selected.revision)
    const preview = data.objects?.find(doc => doc.kind === 'preview' && matches(doc))
    const checked = data.objects?.find(doc => doc.kind === 'content_check' && matches(doc))
    const demand = data.objects?.find(doc => doc.kind === 'demand')
    const demandRef = selected?.body.demand_ref as Ref | undefined
    // A package built from an older demand cannot start; prepare again instead of offering it.
    const current = demand && demandRef?.object_id === demand.object_id && demandRef.revision === demand.revision
    setPrepared(selected && preview && checked && current ? {
      course_ref: ref(selected), preview_ref: ref(preview), check_ref: ref(checked),
      preparation: 'drafted', start: 'pending',
      unverified: ['actual_api', 'voice', 'child_entry'],
    } : undefined)
  }, [token])
  const run = async (operation: () => Promise<void>) => {
    setBusy(true); setMessage('')
    try { await operation(); failedOperation.current = null; await refresh() }
    catch (error) {
      const failure = error as RequestFailure
      failedOperation.current = failure.unknown ? operation : null
      setMessage(failure.detail && failure.detail !== failure.message ? `${failure.message}（${failure.detail}）` : failure.message)
    }
    finally { setBusy(false) }
  }
  useEffect(() => { void refresh().catch(error => setMessage((error as Error).message)) }, [refresh])
  const facts = dashboard?.objects?.filter(doc => doc.kind === 'fact').sort((a, b) => b.created_at.localeCompare(a.created_at)) ?? []
  const fact = facts[0]
  const activity = fact ? String(fact.body.activity_id) : undefined
  const mode = dashboard?.realm === 'adult_check' ? '成人检查' : '模拟'
  const evaluationDoc = activity ? dashboard?.objects?.find(doc => doc.kind === 'evaluation' && doc.body.activity_id === activity) : undefined
  const feedbackChosen = (dashboard?.objects ?? []).some(doc => doc.body.activity_id === activity && (doc.kind === 'feedback' || doc.body.later_explicit === true))
  const pendingSeals = dashboard?.pending_seals ?? []
  const courses = dashboard?.objects?.filter(doc => doc.kind === 'course') ?? []
  const selectedCourse = courses.find(doc => doc.object_id === prepared?.course_ref.object_id)
  const previews = dashboard?.objects?.filter(doc => doc.kind === 'preview') ?? []
  // Each discussion round adds a course revision; its preview names that exact revision.
  const previewDoc = previews.find(doc => { const target = doc.body.course_ref as Ref | undefined
    return target?.object_id === prepared?.course_ref.object_id && target?.revision === prepared?.course_ref.revision })
  const preview = (previewDoc?.body.content ?? selectedCourse?.body.parent_preview) as Record<string, unknown> | undefined
  const confirmed = Boolean(previewDoc?.body.confirmation)
  const courseRequest = () => ({ request_id: uid(), learner_id: learner, course_ref: prepared?.course_ref })
  const resources = (previewDoc?.body.resources ?? []) as Resource[]
  const missing = previewDoc ? missingResources(previewDoc.body) : []
  // Request bodies are made once, so resuming a failed operation repeats the same request.
  const generate = (item: Resource) => {
    const body = { ...courseRequest(), resource_id: item.resource_id }
    return async () => { await api(token, 'parent/generate-resource', body) }
  }
  const adopt = (item: Resource, version: number) => {
    const body = { ...courseRequest(), resource_id: item.resource_id, version }
    return async () => { await api(token, 'parent/adopt-resource', body) }
  }
  const look = (item: Resource, version: number) => async () => {
    const course = prepared!.course_ref
    const query = new URLSearchParams({ learner_id: learner, object_id: course.object_id, revision: String(course.revision),
      resource_id: item.resource_id, version: String(version) })
    const url = await fileUrl(token, `/api/parent/resource-file?${query}`)
    if (item.kind === 'audio') { listening.current?.pause(); listening.current = new Audio(url); await listening.current.play() }
    else setShown(current => ({ ...current, [item.resource_id]: url }))
  }
  const generateAll = () => {
    const steps = resources.filter(item => generatable.includes(item.kind) && !item.versions?.length).map(generate)
    return async () => { for (const step of steps) await step() }
  }
  const parentControl = (command: Control['command']) => {
    const body = { command, event_id: uid(), activity_id: dashboard?.active?.activity_id }
    void run(async () => { await api(token, 'parent/control', body) })
  }
  const submitFeedback = (text: string, correction = false) => {
    if (!activity) return
    const requestId = uid()
    const body = { request_id: requestId, learner_id: learner, activity_id: activity, text,
      corrections: correction ? [{ note: text }] : [] }
    void run(async () => {
      await api(token, 'parent/feedback', body)
      const result = await api(token, 'parent/next-course', { request_id: `${requestId}:next`, learner_id: learner, activity_id: activity })
      setReport(result); setFeedback('')
    })
  }
  return <main>
    <header><span className="eyebrow">{dashboard?.realm === 'adult_check' ? 'PROJECT J · 成人实际检查' : 'PROJECT J · 隔离模拟'}</span><h1>Jane 的家长工作台</h1>
      <p>虚构学习者 · 本课到下一课程包 · 真实 API、声音和儿童入口未验证</p></header>
    {message && <div className="notice" role="alert">{message}</div>}
    {failedOperation.current && <button onClick={() => void run(failedOperation.current!)} disabled={busy}>接续原操作</button>}
    <section><div className="section-title"><h2>准备与开始</h2><button className="quiet" onClick={() => void run(refresh)} disabled={busy}>刷新状态</button></div>
      <p>{dashboard?.verification?.result === 'pass' ? '已有与当前构建匹配的隔离程序检查依据。' : '隔离程序检查尚未取得匹配依据，开始保持待处理。'}</p>
      {modelLines(dashboard?.models as Record<string, any> | undefined).map(line => <p key={line}>{line}</p>)}
      {dashboard?.active ? <><p>课堂状态：{stateLabels[dashboard.active.activity_state]}。工作台不访问课中数据库。</p>
        <div className="row"><button onClick={() => parentControl('pause')}>暂停</button><button onClick={() => parentControl('resume')}>恢复</button>
          <button className="quiet" onClick={() => parentControl('skip')}>跳过当前题</button><button className="quiet" onClick={() => parentControl('close')}>收尾</button>
          <button className="quiet" onClick={() => parentControl('end_unconfirmed')}>结束并保留未确认</button>
          <button className="danger" onClick={() => parentControl('stop')}>立即停止</button></div></> : <>
        <button disabled={busy} onClick={() => { const requestId = uid(); void run(async () => setPrepared(await api<PrepareResult>(token, 'parent/prepare', { request_id: requestId, learner_id: learner }))) }}>准备课程包</button>
        {prepared && <><div className="preview"><h3>本课安排</h3>{preview
          ? <dl>{Object.entries(preview).sort(([a], [b]) => previewRank(a) - previewRank(b)).map(([key, value]) => <div key={key}><dt>{previewLabels[key] ?? key}</dt><dd>{show(value)}</dd></div>)}</dl>
          : <p>预览待取得</p>}
          {previewDoc?.body.note ? <p>说明：{String(previewDoc.body.note)}</p> : null}
          {previewDoc && <><h3>教学资源清单</h3>
            {confirmed && resources.some(item => generatable.includes(item.kind) && !item.versions?.length)
              && <button className="quiet" disabled={busy} onClick={() => void run(generateAll())}>生成全部待生成的资源</button>}
            {resources.length ? <ul>{resourceLines(previewDoc.body).map((line, index) => {
              const item = resources[index], latest = item.versions?.length ?? 0
              return <li key={item.resource_id}>{line}
                {confirmed && generatable.includes(item.kind) && <div className="row">
                  <button className="quiet" disabled={busy} onClick={() => void run(generate(item))}>{latest ? '重新生成' : '生成'}</button>
                  {latest > 0 && <><button className="quiet" disabled={busy} onClick={() => void run(look(item, latest))}>{item.kind === 'audio' ? '试听' : '查看'}第{latest}版</button>
                    <button disabled={busy || item.adopted_version === latest} onClick={() => void run(adopt(item, latest))}>采用第{latest}版</button></>}
                </div>}
                {shown[item.resource_id] && <img className="resource-preview" src={shown[item.resource_id]} alt={item.purpose} />}
              </li>
            })}</ul> : <p>本课不需要额外资源。</p>}
            <dl>{discussionLines(previewDoc.body).map(([label, value], index) => <div key={index}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></>}
          <label>修改意见（改目标、30分钟预算或主要教学方式需回到学习规划）<textarea value={courseFeedback} onChange={event => setCourseFeedback(event.target.value)} /></label>
          <div className="row"><button className="quiet" disabled={busy || !courseFeedback.trim()} onClick={() => void run(async () => {
            setPrepared(await api<PrepareResult>(token, 'parent/discuss', { ...courseRequest(), feedback: courseFeedback.trim() })); setCourseFeedback('')
          })}>提交修改意见</button>
          <button disabled={busy || confirmed} onClick={() => void run(async () => { await api(token, 'parent/confirm', courseRequest()) })}>{confirmed ? '已确认这一版本' : '确认课程'}</button></div></div>
          <h3>本次{mode}条件报告</h3><div className="row">{conditionChoices.map(([key, name, options]) => <label key={key}>{name}
            <select value={conditions[key]} onChange={event => setConditions(current => ({ ...current, [key]: event.target.value }))}>
              {options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>)}</div>
          <label>补充说明（可选）<textarea value={household} onChange={event => setHousehold(event.target.value)} /></label>
          {!confirmed && <p>确认课程后才能开始。</p>}
          {confirmed && missing.length > 0 && <p>以下必需资源采用后才能开始：{missing.join('、')}。</p>}
          <button disabled={busy || !confirmed} onClick={() => { const requestId = uid(); void run(async () => {
            const reported = conditionChoices.map(([key, name, options]) => `${name}：${options.find(([value]) => value === conditions[key])?.[1]}`).join('；')
            const data = await api<StartResult>(token, 'parent/start', { request_id: requestId, learner_id: learner, course_ref: prepared.course_ref,
              conditions: { ...conditions, raw_report: household.trim() ? `${reported}；补充：${household.trim()}` : reported, explicit_start: true } })
            if (!data.student_token) throw new Error(startMessage(data))
            const link = `${location.origin}/#student=${data.student_token}`
            setStudentLink(link); setMessage(`${mode}课堂已建立。打开课堂后可直接输入、求助、暂停或停止。`)
          }) }}>采用当前安排，开始{mode}</button></>}
      </>}
      {studentLink && dashboard?.active && <p><a href={studentLink} target="_blank" rel="noopener noreferrer">打开独立的{mode}课堂</a></p>}
    </section>
    <section><h2>结束事实与一次反馈</h2>
      {dashboard?.lost_activities?.map(item => { const id = String(item.activity_id); const reports = (item.reports ?? []) as { text: string; reported_at: string }[]
        return <div className="notice" key={id}><p>{new Date(String(item.requested_at)).toLocaleString()} 开始的{mode}课堂：{String(item.state)}{fact ? '下方显示的不是这一堂。' : ''}</p>
          {reports.map(report => <p key={report.reported_at}>家长补记（{new Date(report.reported_at).toLocaleString()}）：{report.text}</p>)}
          <label>家长补记已知情况（只保存为新的来源，缺口保留，不进入评价）<textarea value={lostDrafts[id] ?? ''} onChange={event => setLostDrafts(drafts => ({ ...drafts, [id]: event.target.value }))} /></label>
          <button className="quiet" disabled={busy || !lostDrafts[id]?.trim()} onClick={() => { const requestId = uid(); void run(async () => {
            await api(token, 'parent/lost-report', { request_id: requestId, learner_id: learner, activity_id: id, text: lostDrafts[id] })
            setLostDrafts(drafts => ({ ...drafts, [id]: '' })); setMessage('家长补记已保存；缺口保留，未调用评价。')
          }) }}>保存家长补记</button></div> })}
      {pendingSeals.length > 0 && <div className="notice">{activity && pendingSeals.includes(activity)
        ? '最近一堂的结束事实已写入，但保存回执尚未确认；请查询并接续原封存。'
        : `最近结束的一堂课，结束事实尚未确认保存${fact ? '；下方显示的是此前已保存的一堂' : ''}。请查询并接续原封存，不要重新开课补造。`}</div>}
      {fact ? <><p>结束事实已保存。原因：{(reason => endLabels[reason] ?? reason)(String((fact.body.end as Record<string, unknown>)?.reason ?? '未知'))}；活动没有新增儿童学习证据。</p>
        <label>{dashboard?.realm === 'adult_check' ? '补充实际条件或更正（成人检查，虚构学习者）' : '补充实际条件或更正（仅限虚构模拟）'}<textarea placeholder="帮助、代答、离屏结果或体验；未提交内容只留在当前页面。" value={feedback} onChange={event => setFeedback(event.target.value)} /></label>
        <div className="row"><button disabled={busy || !feedback.trim()} onClick={() => submitFeedback(feedback)}>提交反馈并准备下一课</button>
          <button disabled={busy} onClick={() => submitFeedback('无额外补充')}>无额外补充，准备下一课</button>
          <button className="quiet" disabled={busy || !feedback.trim()} onClick={() => submitFeedback(feedback, true)}>追加事实更正并复核</button>
          <button className="quiet" disabled={busy || feedbackChosen} onClick={() => { const requestId = uid(); void run(async () => {
            await api(token, 'parent/later-feedback', { request_id: requestId, learner_id: learner, activity_id: activity })
            setMessage('已记录稍后反馈；这一步没有调用评价模型。')
          }) }}>稍后反馈</button>
          <button className="quiet" disabled={busy || !feedbackChosen} onClick={() => { const requestId = uid(); void run(async () => setReport(await api(token, 'parent/next-course', { request_id: requestId, learner_id: learner, activity_id: activity }))) }}>{feedbackChosen ? '按需准备下一课' : '按需准备下一课（先选稍后反馈）'}</button></div>
        <details><summary>查看原答、帮助、交付与结束事实</summary><pre>{show(fact.body)}</pre></details></> : <p>{dashboard?.note ?? '课堂结束后先封存事实，不等待反馈或模型评价。'}</p>}
      {(dashboard?.pending_seals?.length ?? 0) > 0 || Object.keys(dashboard?.seal_errors ?? {}).length > 0 ?
        <button disabled={busy} onClick={() => void run(async () => { await api(token, 'parent/retry-seal', {}) })}>查询并接续原封存</button> : null}
    </section>
    <section><h2>实际处理与下一课程包</h2>
      {report ? <div className="preview"><dl>{describe(report, dashboard?.objects ?? []).map(([label, text], index) => <div key={index}><dt>{label}</dt><dd>{text}</dd></div>)}</dl>
        <details><summary>原始结果</summary><pre>{show(report)}</pre></details></div> : <p>反馈保存、评价、状态／计划采用、D2 和 C2 分别报告；下节建议不能代替课程包。</p>}
      {evaluationDoc && <div className="preview"><h3>课后评价</h3><dl>{evaluationLines(evaluationDoc, dashboard?.objects ?? []).map(([label, text], index) => <div key={index}><dt>{label}</dt><dd>{text}</dd></div>)}</dl></div>}
      <details><summary>查看已保存对象与确切版本</summary>{dashboard?.objects?.map(doc => <article key={`${doc.object_id}:${doc.revision}`}><h3>{doc.kind} · 修订 {doc.revision}</h3><pre>{show(doc.body)}</pre></article>)}</details>
    </section>
    <section><h2>计划复盘与维护</h2><div className="row">{(['week', 'stage', 'annual', 'long_term'] as const).map((level, index) =>
      <button className="quiet" key={level} disabled={busy} onClick={() => { const requestId = uid(); void run(async () => setReport(await api(token, 'parent/review', { request_id: requestId, learner_id: learner, level }))) }}>{['周复盘', '阶段复盘', '年度复盘', '长期回看'][index]}</button>)}</div>
      <p>复盘复用原证据；日期到达不意味着完成。重要变化须绑定具体方案决定。</p>
      <label>{mode}重要计划方案<textarea value={planGoal} onChange={event => setPlanGoal(event.target.value)} /></label>
      <button className="quiet" disabled={busy || !planGoal.trim()} onClick={() => { const requestId = uid(); void run(async () => setReport(await api(token, 'parent/review', { request_id: requestId, learner_id: learner, level: 'week', proposed_patch: { main_goal: { dimension_id: 'english.reading.facts', description: planGoal } } }))) }}>形成具体方案，暂不生效</button>
      {dashboard?.objects?.filter(doc => doc.kind === 'decision' && doc.body.decision_state === 'pending').map(proposal => {
        // Only current revisions are listed, so a baseline missing from them has changed since the proposal.
        const stale = [proposal.body.plan_ref, proposal.body.state_ref].some(target => !dashboard?.objects?.some(doc =>
          doc.object_id === (target as Ref).object_id && doc.revision === (target as Ref).revision))
        return <article key={proposal.object_id}>
        <h3>待决定的具体方案</h3>{stale && <p className="notice">计划或状态已在此方案之后更新，此版本只能拒绝；需要时重新形成方案。</p>}<div className="preview"><dl>{([
          ['方案内容', (proposal.body.patch as { main_goal?: { description?: string } } | null)?.main_goal?.description ?? show(proposal.body.patch)],
          ['理由', proposal.body.reason], ['影响', proposal.body.impact],
          ['其他选择', (proposal.body.alternatives as string[] | undefined)?.join('；')], ['复核条件', proposal.body.review_condition],
        ] as [string, unknown][]).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{show(value ?? '无')}</dd></div>)}</dl></div><div className="row">{(['accept', 'reject'] as const).map(choice => <button className="quiet" key={choice} disabled={busy || (stale && choice === 'accept')} onClick={() => { const requestId = uid(); void run(async () => {
          const result = await api(token, 'parent/decision', { request_id: requestId, learner_id: learner, decision_ref: ref(proposal), choice }); setReport(result)
          if (choice === 'accept') await api(token, 'parent/recheck-demand', { request_id: `${requestId}:demand`, learner_id: learner })
        }) }}>{choice === 'accept' ? '接受此版本并复核需求' : '拒绝此版本'}</button>)}</div></article>
      })}
      <div className="row"><button className="quiet" disabled={busy} onClick={() => { const requestId = uid(); void run(async () => {
        const data = await api<{path: string}>(token, 'parent/export', { request_id: requestId, learner_id: learner })
        const response = await fetch(`/api/parent/export-file?path=${encodeURIComponent(data.path)}`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' })
        if (!response.ok) throw new Error('导出未取得')
        const url = URL.createObjectURL(await response.blob()); const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'project-j-simulation.json'; anchor.click(); URL.revokeObjectURL(url)
      }) }}>导出{mode}记录</button>
      <button className="quiet" disabled={busy} onClick={() => void run(async () => { const data = await api<{backup_id: string}>(token, 'parent/backup', {}); setBackupId(data.backup_id); setMessage('一致数据库与固定素材已备份。') })}>备份</button>
      <button className="quiet" disabled={busy || !backupId} onClick={() => { const newRun = `restored-${uid()}`; void run(async () => { const data = await api(token, 'parent/restore', { backup_id: backupId, new_run_id: newRun }); setReport(data) }) }}>恢复到新{mode}目录</button></div>
    </section>
  </main>
}

function Student({ token }: { token: string }) {
  const [snapshot, setSnapshot] = useState<Snapshot>()
  const [text, setText] = useState('')
  const [message, setMessage] = useState('')
  const [inFlight, setInFlight] = useState(0)
  const busy = inFlight > 0
  const [cancellation, setCancellation] = useState<Cancellation>()
  const [lastInput, setLastInput] = useState('')
  const [failedRequest, setFailedRequest] = useState<{ path: string; payload: unknown }>()
  const [failedDelivery, setFailedDelivery] = useState<DeliveryFeedback>()
  const [recorder, setRecorder] = useState<Recorder>(idle)
  const [readingText, setReadingText] = useState('')
  const reading = snapshot?.recording
  const delivered = useRef(new Set<string>())
  // Adult check: Jane speaks first and her text appears only after the audio (parent's decision).
  const adult = snapshot?.realm === 'adult_check'
  const speaker = useRef<Speaker | null>(null)
  const presenting = useRef<Promise<void>>(Promise.resolve())
  const playing = useRef<string | null>(null)
  const deliveryIds = useRef(new Map<string, string>())
  const started = useRef(new Map<string, Promise<void>>())
  const latest = useRef(snapshot)
  latest.current = snapshot
  const halt = useRef(false)
  const [speaking, setSpeaking] = useState(false)
  const [locked, setLocked] = useState(false)
  const microphone = useRef<Microphone | null>(null)
  const setCurrent = useCallback((value: Snapshot) => setSnapshot(old => old && old.core_revision > value.core_revision ? old : value), [])
  const localCancelled = presentationCancelled(cancellation, snapshot)
  // A pause typed or spoken as an answer reaches the core without the local cancel path.
  const halted = localCancelled || snapshot?.mode === 'paused'
  useEffect(() => {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const socket = new WebSocket(`${protocol}//${location.host}/api/student/events`, `project-j.${token}`)
    socket.onmessage = event => {
      const result = JSON.parse(event.data) as { snapshot?: Snapshot; error?: string }
      if (result.snapshot) setCurrent(result.snapshot)
      if (result.error) setMessage(result.error)
    }
    socket.onerror = () => setMessage('连接状态未知。停止按钮仍可独立使用；刷新只接回原会话。')
    return () => socket.close()
  }, [token, setCurrent])
  const send = async (path: string, payload: unknown) => {
    setInFlight(count => count + 1); setMessage('')
    const previous = lastInput
    // An answer's request returns only after judging; bind it now so "还没说完" can follow it meanwhile.
    if (path === 'student/input') setLastInput((payload as TextInput).event_id)
    try {
      const value = await api<Ack>(token, path, payload)
      setCurrent(value.snapshot); setFailedRequest(undefined)
      return value
    }
    catch (error) {
      const failure = error as RequestFailure
      if (path === 'student/input' && !failure.unknown) setLastInput(previous)
      setFailedRequest(failure.unknown ? { path, payload } : undefined); setMessage(failure.message); return undefined
    }
    finally { setInFlight(count => count - 1) }
  }
  const control = async (command: StudentControl['command']) => {
    const eventId = uid()
    setMessage('')
    if (command === 'stop' || command === 'pause') { halt.current = true; speaker.current?.stop() }
    if (command === 'stop' || command === 'pause') setCancellation(current => cancel(current, eventId, command))
    try {
      const value = await api<Snapshot>(token, 'student/control', { event_id: eventId, command })
      if (command === 'pause') setCancellation(current => acknowledgePause(current, eventId, value))
      setCurrent(value)
    } catch (error) {
      const failure = error as RequestFailure
      setMessage(command !== 'stop' && command !== 'pause' ? failure.message
        : failure.unknown ? '本端已停止呈现；核心回执未取得，可再按一次。' : `本端已停止呈现；${failure.message}`)
    }
  }
  const sendDelivery = (payload: DeliveryFeedback) => api<Snapshot>(token, 'student/delivery', payload).then(setCurrent).catch(error => {
    const failure = error as RequestFailure
    if (failure.unknown) setFailedDelivery(payload)
    setMessage(failure.unknown ? '呈现回执未取得；请接续原呈现回执。' : failure.message)
  })
  useEffect(() => {
    if (!adult || speaker.current) return
    const created = new Speaker(token, (action, at) => {
      const id = deliveryIds.current.get(action.action_id)
      if (id) started.current.set(id, sendDelivery(startedReport(action, id, at)))
    })
    speaker.current = created
    setLocked(created.locked)
    return () => { created.close(); speaker.current = null }
  }, [adult, token])
  useEffect(() => () => { speaker.current?.stop(); microphone.current?.discard() }, [])
  useEffect(() => {
    if (snapshot && ['paused', 'offscreen', 'ended'].includes(snapshot.mode)) speaker.current?.stop()
  }, [snapshot])
  useEffect(() => {
    const action = snapshot?.pending_action
    if (!action || localCancelled || delivered.current.has(action.action_id)) return
    if (adult) {
      const voice = speaker.current
      if (!voice) return
      delivered.current.add(action.action_id)
      const id = uid()
      deliveryIds.current.set(action.action_id, id)
      halt.current = false
      playing.current = action.action_id
      setSpeaking(true)
      presenting.current = voice.play(action).then(async outcome => {
        if (playing.current === action.action_id) { playing.current = null; setSpeaking(false) }
        const now = latest.current
        const halted = halt.current || !now || ['paused', 'offscreen', 'ended'].includes(now.mode)
          || now.pending_action?.action_id !== action.action_id
        // In order: "started" first, and a text fallback before the child's input does.
        await started.current.get(id)
        started.current.delete(id)
        for (const feedback of reportsFor(action, outcome, halted, id, new Date())) await sendDelivery(feedback)
      })
      return
    }
    delivered.current.add(action.action_id)
    void sendDelivery({ delivery_event_id: uid(), delivery_id: uid(),
      action_id: action.action_id, activity_id: action.activity_id, task_instance_id: action.task_instance_id,
      sequence: 1, output_device_id: 'simulation-browser-text', modality: 'text', status: 'completed',
      delivered_range: ['text_content'], occurred_at: new Date().toISOString() })
  }, [snapshot, token, setCurrent, localCancelled, adult, locked])
  // A child cutting in stops Jane and waits until the full text has been reported.
  const interrupt = async () => {
    if (!adult) return
    speaker.current?.stop()
    await presenting.current
  }
  const input = async (value: string, intent: TextInput['intent'] = 'unknown', supplement = false) => {
    await interrupt()
    const result = await send('student/input', { event_id: uid(), text: value, intent,
      relation: supplement ? 'supplement' : 'new', related_event_id: supplement ? lastInput : null })
    // Keep anything typed while this request was being judged.
    if (result) setText(current => current === value ? '' : current)
  }
  const beginReading = async () => {
    await interrupt()
    if (adult) {
      microphone.current ??= new Microphone()
      try { await microphone.current.start() } catch {
        setMessage('需要允许使用麦克风才能朗读；可在地址栏允许后重试。')
        return
      }
    }
    setRecorder(current => startRecording(current, performance.now()))
  }
  const finishReading = async (now: number) => {
    if (!reading) return
    const timing = finishRecording(recorder, now, Number(reading.max_seconds))
    if (!timing) return
    setRecorder(idle)
    let audio: string
    if (adult) {
      try { audio = toBase64(microphone.current ? await microphone.current.finish() : new Uint8Array()) } catch {
        setMessage('录音无法读取，请重录。')
        return
      }
    }
    // Simulation convention: the recording bytes are the text the fictional child read.
    else audio = btoa(String.fromCharCode(...new TextEncoder().encode(readingText)))
    const result = await send('student/recording', { event_id: uid(), audio_base64: audio, ...timing })
    if (result) setReadingText('')
  }
  // Pausing, stopping or leaving the reading segment ends the take locally without submitting it.
  const readingOpen = Boolean(reading) && !halted
  useEffect(() => { if (!readingOpen) { microphone.current?.discard(); setRecorder(idle) } }, [readingOpen])
  // The maximum ends the take; the latest render's finishReading sees the current text.
  const finishRef = useRef(finishReading)
  finishRef.current = finishReading
  useEffect(() => {
    if (recorder.state !== 'recording' || !reading) return
    const timer = setInterval(() => {
      const now = performance.now()
      if (recordedMs(recorder, now) >= Number(reading.max_seconds) * 1000) void finishRef.current(now)
    }, 250)
    return () => clearInterval(timer)
  }, [recorder, reading])
  return <main className="student">
    <header><span className="eyebrow">{adult ? 'JANE · 成人实际检查' : 'JANE · 虚构课堂'}</span><h1>一起读故事，找依据</h1>
      <p>{adult ? '真实模型与本机声音；先听 Jane 说，说完再显示文字；录音只交本机评分，不保存。' : '文字与技术信号模拟；不会录音或播放真实声音。'}</p></header>
    <div className="controls"><span>{snapshot ? stateLabels[snapshot.activity_state] : '正在连接原会话'}{snapshot?.mode === 'judging' && '：Jane正在处理你的回答'}</span>
      {snapshot?.mode !== 'ended' && <><button className="quiet" disabled={halted} onClick={() => void control('pause')}>暂停</button>
        <button className="danger" onClick={() => void control('stop')}>立即停止</button></>}</div>
    {message && <div className="notice" role="alert">{message}</div>}
    {failedRequest && <button className="quiet" disabled={busy} onClick={() => void send(failedRequest.path, failedRequest.payload)}>接续原提交</button>}
    {failedDelivery && <button className="quiet" onClick={() => {
      const payload = failedDelivery
      void api<Snapshot>(token, 'student/delivery', payload).then(value => {
        setCurrent(value); setFailedDelivery(undefined)
      }).catch(error => {
        const failure = error as RequestFailure
        if (!failure.unknown) setFailedDelivery(undefined)
        setMessage(failure.message)
      })
    }}>接续原呈现回执</button>}
    {localCancelled && <p className="notice">本端已停止呈现。暂停后由家长在工作台恢复；停止后不能恢复。</p>}
    <section className="jane"><span className="avatar" aria-hidden="true">J</span><div><h2>Jane</h2><p aria-live="polite">{localCancelled ? '呈现已取消。' : !adult
      ? (snapshot?.pending_action ?? snapshot?.presented_action)?.text ?? '等待同一核心的下一动作。'
      : locked ? '请点“打开声音”，听 Jane 说话。' : speaking ? 'Jane 正在说话……' : snapshot?.presented_action?.text ?? '等待同一核心的下一动作。'}</p>
      {adult && locked && <button onClick={() => void speaker.current?.resume().then(() => setLocked(false))}>打开声音</button>}</div></section>
    {!localCancelled && <Visuals token={token} action={!adult ? (snapshot?.pending_action ?? snapshot?.presented_action) ?? undefined
      : locked || speaking ? undefined : snapshot?.presented_action ?? undefined} />}
    {!localCancelled && Object.entries(snapshot?.materials ?? {}).map(([id, material]) => <section key={id} className="reading"><pre>{material}</pre></section>)}
    {reading && snapshot?.mode !== 'ended' && <section className="reading-aloud">
      <h2>朗读这一段</h2><p>{String(reading.reference_text)}</p>
      {!adult && <label>模拟朗读内容（不使用麦克风，录音只交发音评分）<textarea value={readingText} onChange={event => setReadingText(event.target.value)} disabled={halted} /></label>}
      <p>{{ idle: '未开始', recording: '录音中', paused: '已暂停' }[recorder.state]}</p>
      <div className="row">
        {recorder.state === 'idle' && <button disabled={busy || halted} onClick={() => void beginReading()}>开始录音</button>}
        {recorder.state === 'recording' && <button className="quiet" onClick={() => { microphone.current?.pause(); setRecorder(pauseRecording(recorder, performance.now())) }}>暂停录音</button>}
        {recorder.state === 'paused' && <button className="quiet" onClick={() => { microphone.current?.resume(); setRecorder(resumeRecording(recorder, performance.now())) }}>继续录音</button>}
        {recorder.state !== 'idle' && <><button disabled={busy} onClick={() => void finishReading(performance.now())}>结束并提交</button>
          <button className="quiet" onClick={() => { microphone.current?.discard(); setRecorder(idle) }}>重录</button></>}
      </div></section>}
    {snapshot?.mode === 'offscreen' ? <section><p>离屏期间没有采集。回来后需要报告实际条件与结果，返回本身不算完成。</p><button onClick={() => void control('return')}>返回，报告结果</button></section> :
      snapshot?.mode !== 'ended' && <section><label>你的虚构回答或报告<textarea value={text} onChange={event => setText(event.target.value)} disabled={localCancelled} /></label>
        <div className="row"><button disabled={busy || !text.trim() || halted || Boolean(reading)} onClick={() => void input(text)}>提交</button>
          <button className="quiet" disabled={busy || halted} onClick={() => void input('帮我一下', 'help')}>帮我一下</button>
          {canConfirm(snapshot) && <button className="quiet" disabled={busy || halted} onClick={() => void input('听到了，收到当前说明。', 'confirmation')}>确认收到说明</button>}
          <button className="quiet" disabled={!lastInput || !text.trim() || halted} onClick={() => void input(text, 'unknown', true)}>还没说完，补充</button>
          <button className="quiet" disabled={halted} onClick={() => void control('repeat')}>重复当前内容</button></div></section>}
    {snapshot?.mode === 'ended' && <section><h2>本堂已结束</h2>{snapshot.end && <p>结束原因：{endLabels[String(snapshot.end.reason)] ?? String(snapshot.end.reason)}。</p>}<p>只保留已发生范围；事实封存独立进行，家长可随后反馈。</p></section>}
  </main>
}

export default function App() {
  const params = new URLSearchParams(location.hash.slice(1))
  const parent = params.get('parent'); const student = params.get('student')
  return student ? <Student token={student} /> : parent ? <Parent token={parent} /> :
    <main><header><span className="eyebrow">PROJECT J</span><h1>隔离模拟工作台</h1></header><p>请使用本机启动入口给出的工作台链接。真实儿童入口保持关闭。</p></main>
}
