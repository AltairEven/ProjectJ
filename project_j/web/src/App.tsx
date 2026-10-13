import { useCallback, useEffect, useRef, useState } from 'react'
import { acknowledgePause, cancel, presentationCancelled, type Cancellation } from './cancellation'
import type { Snapshot, Dashboard, RevisionRef as Ref, Document as Doc, Control, StudentControl, DeliveryFeedback, TextInput, VoiceSignal, PreparationResult as PrepareResult, StartResult, InputResult as Ack } from './client/types.gen'

const learner = 'fictional-L1'
const uid = () => crypto.randomUUID()
const show = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2)
const ref = (doc: Doc): Ref => ({ object_id: doc.object_id, revision: doc.revision })

async function api<T>(token: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.message ?? show(result.detail) ?? '操作未完成')
  return result as T
}

function Parent({ token }: { token: string }) {
  const [dashboard, setDashboard] = useState<Dashboard>()
  const [prepared, setPrepared] = useState<PrepareResult>()
  const [feedback, setFeedback] = useState('')
  const [report, setReport] = useState<unknown>()
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [backupId, setBackupId] = useState('')
  const [household, setHousehold] = useState('模拟：纸、书和小车已摆好，有30分钟，家长可陪伴。')
  const [studentLink, setStudentLink] = useState('')
  const [planGoal, setPlanGoal] = useState('在相近新材料中观察阅读依据，继续保留短英语报告支架。')
  const failedOperation = useRef<(() => Promise<void>) | null>(null)
  const refresh = useCallback(async () => {
    const data = await api<Dashboard>(token, `parent/dashboard?learner_id=${learner}`)
    setDashboard(data)
    const courses = (data.objects ?? []).filter(doc => doc.kind === 'course').sort((a, b) => b.created_at.localeCompare(a.created_at))
    const selected = courses[0]
    const matches = (doc: Doc) => selected && doc.dependency_refs?.some(
      dep => dep.object_id === selected.object_id && dep.revision === selected.revision)
    const preview = data.objects?.find(doc => doc.kind === 'preview' && matches(doc))
    const checked = data.objects?.find(doc => doc.kind === 'content_check' && matches(doc))
    setPrepared(selected && preview && checked ? {
      course_ref: ref(selected), preview_ref: ref(preview), check_ref: ref(checked),
      preparation: 'fixed_simulation_checked', start: 'pending',
      unverified: ['actual_api', 'voice', 'child_entry'],
    } : undefined)
  }, [token])
  const run = async (operation: () => Promise<void>) => {
    setBusy(true); setMessage('')
    try { await operation(); failedOperation.current = null; await refresh() }
    catch (error) { failedOperation.current = operation; setMessage((error as Error).message) }
    finally { setBusy(false) }
  }
  useEffect(() => { void refresh().catch(error => setMessage((error as Error).message)) }, [refresh])
  const facts = dashboard?.objects?.filter(doc => doc.kind === 'fact').sort((a, b) => b.created_at.localeCompare(a.created_at)) ?? []
  const fact = facts[0]
  const activity = fact ? String(fact.body.activity_id) : undefined
  const courses = dashboard?.objects?.filter(doc => doc.kind === 'course') ?? []
  const selectedCourse = courses.find(doc => doc.object_id === prepared?.course_ref.object_id)
  const previews = dashboard?.objects?.filter(doc => doc.kind === 'preview') ?? []
  const previewsForCourse = previews.filter(doc => doc.dependency_refs?.some(dep => dep.object_id === prepared?.course_ref.object_id))
  const previewsBody = previewsForCourse[0]?.body.content
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
    <header><span className="eyebrow">PROJECT J · 隔离模拟</span><h1>Jane 的家长工作台</h1>
      <p>虚构学习者 · 本课到下一课程包 · 真实 API、声音和儿童入口未验证</p></header>
    {message && <div className="notice" role="alert">{message}</div>}
    {failedOperation.current && <button onClick={() => void run(failedOperation.current!)} disabled={busy}>接续原操作</button>}
    <section><div className="section-title"><h2>准备与开始</h2><button className="quiet" onClick={() => void run(refresh)} disabled={busy}>刷新状态</button></div>
      <p>{dashboard?.verification?.result === 'pass' ? '已有与当前构建匹配的隔离程序检查依据。' : '隔离程序检查尚未取得匹配依据，开始保持待处理。'}</p>
      {dashboard?.active ? <><p>课堂状态：{dashboard.active.activity_state}。工作台不访问课中数据库。</p>
        <div className="row"><button onClick={() => parentControl('pause')}>暂停</button><button onClick={() => parentControl('resume')}>恢复</button>
          <button className="quiet" onClick={() => parentControl('skip')}>跳过当前题</button><button className="quiet" onClick={() => parentControl('close')}>收尾</button>
          <button className="quiet" onClick={() => parentControl('end_unconfirmed')}>结束并保留未确认</button>
          <button className="danger" onClick={() => parentControl('stop')}>立即停止</button></div></> : <>
        <button disabled={busy} onClick={() => { const requestId = uid(); void run(async () => setPrepared(await api<PrepareResult>(token, 'parent/prepare', { request_id: requestId, learner_id: learner }))) }}>准备课程包</button>
        {prepared && <><div className="preview"><h3>本课安排</h3><pre>{show(previewsBody ?? selectedCourse?.body.parent_preview ?? '预览待取得')}</pre></div>
          <label>本次模拟条件报告<textarea value={household} onChange={event => setHousehold(event.target.value)} /></label>
          <button disabled={busy || !household.trim()} onClick={() => { const requestId = uid(); void run(async () => {
            const data = await api<StartResult>(token, 'parent/start', { request_id: requestId, learner_id: learner, course_ref: prepared.course_ref,
              conditions: { materials: 'reported_available', time: 'reported_30_minutes', accompaniment: 'reported_present', raw_report: household, explicit_start: true } })
            if (!data.student_token) throw new Error(show(data.missing?.length ? data.missing : data.reason ?? data.decision))
            const link = `${location.origin}/#student=${data.student_token}`
            setStudentLink(link); setMessage('模拟课堂已建立。打开课堂后可直接输入、求助、暂停或停止。')
          }) }}>采用当前安排，开始模拟</button></>}
      </>}
      {studentLink && <p><a href={studentLink} target="_blank" rel="noopener noreferrer">打开独立的模拟课堂</a></p>}
    </section>
    <section><h2>结束事实与一次反馈</h2>
      {fact ? <><p>结束事实已保存。原因：{String((fact.body.end as Record<string, unknown>)?.reason ?? '未知')}；活动没有新增儿童学习证据。</p>
        <label>补充实际条件或更正（仅限虚构模拟）<textarea placeholder="帮助、代答、离屏结果或体验；未提交内容只留在当前页面。" value={feedback} onChange={event => setFeedback(event.target.value)} /></label>
        <div className="row"><button disabled={busy || !feedback.trim()} onClick={() => submitFeedback(feedback)}>提交反馈并准备下一课</button>
          <button disabled={busy} onClick={() => submitFeedback('无额外补充')}>无额外补充，准备下一课</button>
          <button className="quiet" disabled={busy || !feedback.trim()} onClick={() => submitFeedback(feedback, true)}>追加事实更正并复核</button>
          <button className="quiet" disabled={busy} onClick={() => { const requestId = uid(); void run(async () => {
            await api(token, 'parent/later-feedback', { request_id: requestId, learner_id: learner, activity_id: activity })
            setMessage('已记录稍后反馈；这一步没有调用评价模型。')
          }) }}>稍后反馈</button>
          <button className="quiet" disabled={busy} onClick={() => { const requestId = uid(); void run(async () => setReport(await api(token, 'parent/next-course', { request_id: requestId, learner_id: learner, activity_id: activity }))) }}>按需准备下一课</button></div>
        <details><summary>查看原答、帮助、交付与结束事实</summary><pre>{show(fact.body)}</pre></details></> : <p>课堂结束后先封存事实，不等待反馈或模型评价。</p>}
      {(dashboard?.pending_seals?.length ?? 0) > 0 || Object.keys(dashboard?.seal_errors ?? {}).length > 0 ?
        <button disabled={busy} onClick={() => void run(async () => { await api(token, 'parent/retry-seal', {}) })}>查询并接续原封存</button> : null}
    </section>
    <section><h2>实际处理与下一课程包</h2>
      {report ? <pre>{show(report)}</pre> : <p>反馈保存、评价、状态／计划采用、D2 和 C2 分别报告；下节建议不能代替课程包。</p>}
      <details><summary>查看已保存对象与确切版本</summary>{dashboard?.objects?.map(doc => <article key={`${doc.object_id}:${doc.revision}`}><h3>{doc.kind} · 修订 {doc.revision}</h3><pre>{show(doc.body)}</pre></article>)}</details>
    </section>
    <section><h2>计划复盘与维护</h2><div className="row">{(['week', 'stage', 'annual', 'long_term'] as const).map((level, index) =>
      <button className="quiet" key={level} disabled={busy} onClick={() => { const requestId = uid(); void run(async () => setReport(await api(token, 'parent/review', { request_id: requestId, learner_id: learner, level }))) }}>{['周复盘', '阶段复盘', '年度复盘', '长期回看'][index]}</button>)}</div>
      <p>复盘复用原证据；日期到达不意味着完成。重要变化须绑定具体方案决定。</p>
      <label>模拟重要计划方案<textarea value={planGoal} onChange={event => setPlanGoal(event.target.value)} /></label>
      <button className="quiet" disabled={busy || !planGoal.trim()} onClick={() => { const requestId = uid(); void run(async () => setReport(await api(token, 'parent/review', { request_id: requestId, learner_id: learner, level: 'week', proposed_patch: { main_goal: { dimension_id: 'english.reading.facts', description: planGoal } } }))) }}>形成具体方案，暂不生效</button>
      {dashboard?.objects?.filter(doc => doc.kind === 'decision' && doc.body.decision_state === 'pending').map(proposal => <article key={proposal.object_id}>
        <h3>待决定的具体方案</h3><pre>{show(proposal.body)}</pre><div className="row">{(['accept', 'reject'] as const).map(choice => <button className="quiet" key={choice} disabled={busy} onClick={() => { const requestId = uid(); void run(async () => {
          const result = await api(token, 'parent/decision', { request_id: requestId, learner_id: learner, decision_ref: ref(proposal), choice }); setReport(result)
          if (choice === 'accept') await api(token, 'parent/recheck-demand', { request_id: `${requestId}:demand`, learner_id: learner })
        }) }}>{choice === 'accept' ? '接受此版本并复核需求' : '拒绝此版本'}</button>)}</div></article>)}
      <div className="row"><button className="quiet" disabled={busy} onClick={() => { const requestId = uid(); void run(async () => {
        const data = await api<{path: string}>(token, 'parent/export', { request_id: requestId, learner_id: learner })
        const response = await fetch(`/api/parent/export-file?path=${encodeURIComponent(data.path)}`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' })
        if (!response.ok) throw new Error('导出未取得')
        const url = URL.createObjectURL(await response.blob()); const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'project-j-simulation.json'; anchor.click(); URL.revokeObjectURL(url)
      }) }}>导出模拟记录</button>
      <button className="quiet" disabled={busy} onClick={() => void run(async () => { const data = await api<{backup_id: string}>(token, 'parent/backup', {}); setBackupId(data.backup_id); setMessage('一致数据库与固定素材已备份。') })}>备份</button>
      <button className="quiet" disabled={busy || !backupId} onClick={() => { const newRun = `restored-${uid()}`; void run(async () => { const data = await api(token, 'parent/restore', { backup_id: backupId, new_run_id: newRun }); setReport(data) }) }}>恢复到新模拟目录</button></div>
    </section>
  </main>
}

function Student({ token }: { token: string }) {
  const [snapshot, setSnapshot] = useState<Snapshot>()
  const [text, setText] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [cancellation, setCancellation] = useState<Cancellation>()
  const [voiceItem, setVoiceItem] = useState('')
  const [voiceText, setVoiceText] = useState('')
  const [lastInput, setLastInput] = useState('')
  const [failedRequest, setFailedRequest] = useState<{ path: string; payload: unknown }>()
  const [failedDelivery, setFailedDelivery] = useState<DeliveryFeedback>()
  const delivered = useRef(new Set<string>())
  const setCurrent = useCallback((value: Snapshot) => setSnapshot(old => old && old.core_revision > value.core_revision ? old : value), [])
  const localCancelled = presentationCancelled(cancellation, snapshot)
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
    setBusy(true); setMessage('')
    try {
      const value = await api<Ack>(token, path, payload)
      setCurrent(value.snapshot); setFailedRequest(undefined)
      if (path === 'student/input') setLastInput((payload as TextInput).event_id)
      return value
    }
    catch (error) { setFailedRequest({ path, payload }); setMessage((error as Error).message); return undefined }
    finally { setBusy(false) }
  }
  const control = async (command: StudentControl['command']) => {
    const eventId = uid()
    if (command === 'stop' || command === 'pause') setCancellation(current => cancel(current, eventId, command))
    try {
      const value = await api<Snapshot>(token, 'student/control', { event_id: eventId, command })
      if (command === 'pause') setCancellation(current => acknowledgePause(current, eventId, value))
      setCurrent(value)
    } catch (error) { setMessage(`本端已停止呈现；核心回执未取得：${(error as Error).message}`) }
  }
  useEffect(() => {
    const action = snapshot?.pending_action
    if (!action || localCancelled || delivered.current.has(action.action_id)) return
    delivered.current.add(action.action_id)
    const payload: DeliveryFeedback = { delivery_event_id: uid(), delivery_id: uid(),
      action_id: action.action_id, activity_id: action.activity_id, task_instance_id: action.task_instance_id,
      sequence: 1, output_device_id: 'simulation-browser-text', modality: 'text', status: 'completed',
      delivered_range: ['text_content'], occurred_at: new Date().toISOString() }
    void api<Snapshot>(token, 'student/delivery', payload).then(setCurrent).catch(error => {
      setFailedDelivery(payload); setMessage(`呈现回执未知：${(error as Error).message}`)
    })
  }, [snapshot, token, setCurrent, localCancelled])
  const [lastAction, setLastAction] = useState<Snapshot['pending_action']>()
  useEffect(() => { if (snapshot?.pending_action && !localCancelled) setLastAction(snapshot.pending_action) }, [snapshot, localCancelled])
  const input = async (value: string, intent: TextInput['intent'] = 'unknown', supplement = false) => {
    const event = uid()
    const result = await send('student/input', { event_id: event, text: value, intent,
      relation: supplement ? 'supplement' : 'new', related_event_id: supplement ? lastInput : null })
    if (result) { setLastInput(event); setText('') }
  }
  const voice = async (kind: VoiceSignal['kind']) => {
    const item = kind === 'start' ? uid() : voiceItem
    if (kind === 'start') setVoiceItem(item)
    await send('student/voice-signal', { kind, item_id: item, text: voiceText,
      candidate_end_ms: kind === 'candidate_end' ? performance.now() : null, source: 'simulated_speaker' })
  }
  return <main className="student">
    <header><span className="eyebrow">JANE · 虚构课堂</span><h1>一起读故事，找依据</h1><p>文字与技术信号模拟；不会录音或播放真实声音。</p></header>
    <div className="controls"><span>{snapshot?.activity_state ?? '正在连接原会话'}</span>
      <button className="quiet" onClick={() => void control('pause')}>暂停</button>
      <button className="danger" onClick={() => void control('stop')}>立即停止</button></div>
    {message && <div className="notice" role="alert">{message}</div>}
    {failedRequest && <button className="quiet" disabled={busy} onClick={() => void send(failedRequest.path, failedRequest.payload)}>接续原提交</button>}
    {failedDelivery && <button className="quiet" onClick={() => {
      const payload = failedDelivery
      void api<Snapshot>(token, 'student/delivery', payload).then(value => {
        setCurrent(value); setFailedDelivery(undefined)
      }).catch(error => setMessage((error as Error).message))
    }}>接续原呈现回执</button>}
    {localCancelled && <p className="notice">本端已停止呈现。暂停后由家长在工作台恢复；停止后不能恢复。</p>}
    <section className="jane"><span className="avatar" aria-hidden="true">J</span><div><h2>Jane</h2><p aria-live="polite">{localCancelled ? '呈现已取消。' : (snapshot?.pending_action ?? lastAction)?.text ?? '等待同一核心的下一动作。'}</p></div></section>
    {!localCancelled && Object.entries(snapshot?.materials ?? {}).map(([id, material]) => <section key={id} className="reading"><pre>{material}</pre></section>)}
    {snapshot?.mode === 'offscreen' ? <section><p>离屏期间没有采集。回来后需要报告实际条件与结果，返回本身不算完成。</p><button onClick={() => void control('return')}>返回，报告结果</button></section> :
      snapshot?.mode !== 'ended' && <section><label>你的虚构回答或报告<textarea value={text} onChange={event => setText(event.target.value)} disabled={localCancelled} /></label>
        <div className="row"><button disabled={busy || !text.trim() || localCancelled} onClick={() => void input(text)}>提交</button>
          <button className="quiet" disabled={busy || localCancelled} onClick={() => void input('帮我一下', 'help')}>帮我一下</button>
          <button className="quiet" disabled={busy || localCancelled} onClick={() => void input('听到了，收到当前说明。', 'confirmation')}>确认收到说明</button>
          <button className="quiet" disabled={!lastInput || !text.trim() || localCancelled} onClick={() => void input(text, 'unknown', true)}>还没说完，补充</button>
          <button className="quiet" onClick={() => void control('repeat')}>重复当前内容</button></div></section>}
    {snapshot?.mode === 'ended' && <section><h2>本堂已结束</h2><p>只保留已发生范围；事实封存独立进行，家长可随后反馈。</p></section>}
    <details className="technical"><summary>模拟语音信号（不使用麦克风）</summary>
      <label>模拟识别文本<textarea value={voiceText} onChange={event => setVoiceText(event.target.value)} /></label>
      <div className="row">{(['start', 'fragment', 'candidate_end', 'final', 'window_elapsed', 'resume', 'revision', 'cancel'] as const).map((kind, index) =>
        <button className="quiet" disabled={localCancelled || snapshot?.mode === 'ended'} key={kind} onClick={() => void voice(kind)}>{['新语段', '识别片段', '候选结束', '最终识别', '窗口到期', '继续说话', '识别修订', '取消语段'][index]}</button>)}</div>
      <p>片段不评分；窗口到期、最终齐和绑定仍有效才提交。识别修订回连原语段，不新增尝试。</p>
    </details>
  </main>
}

export default function App() {
  const params = new URLSearchParams(location.hash.slice(1))
  const parent = params.get('parent'); const student = params.get('student')
  return student ? <Student token={student} /> : parent ? <Parent token={parent} /> :
    <main><header><span className="eyebrow">PROJECT J</span><h1>隔离模拟工作台</h1></header><p>请使用本机启动入口给出的工作台链接。真实儿童入口保持关闭。</p></main>
}
