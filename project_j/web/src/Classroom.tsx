import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fileUrl, uid } from './api'
import { acknowledgePause, cancel, presentationCancelled, type Cancellation } from './cancellation'
import { canConfirm, confirmationKey } from './confirmation'
import { endLabels } from './report'
import { finishRecording, idle, pauseRecording, recordedMs, resumeRecording, startRecording, type Recorder } from './recording'
import type { RequestFailure } from './request'
import { Speaker, reportsFor, startedReport, visualReport } from './speaker'
import { Microphone } from './microphone'
import { classProgress } from './progress'
import { Progress } from './ui'
import { toBase64 } from './audio'
import type { AllowedAction, Snapshot, Control, DeliveryFeedback, TextInput, InputResult as Ack } from './client/types.gen'

const stateLabels: Record<string, string> = { running: '进行中', offscreen: '离屏活动中', paused: '已暂停', closing: '收尾', ended: '已结束' }

// Pictures and animations the core approved with an action; shown as images only. Once each
// has displayed or failed, onShown names the ones the student could see.
function Visuals({ token, action, onShown }: { token: string, action?: AllowedAction, onShown: (action: AllowedAction, shown: string[]) => void }) {
  const [items, setItems] = useState<{ resource: string, url: string }[]>([])
  const outcomes = useRef(new Map<string, boolean>())
  const id = action?.action_id, visuals = action?.visuals ?? []
  const key = visuals.join(' ')
  const settle = (resource: string, displayed: boolean) => {
    outcomes.current.set(resource, displayed)
    if (action && outcomes.current.size === visuals.length) onShown(action, visuals.filter(item => outcomes.current.get(item)))
  }
  useEffect(() => {
    outcomes.current = new Map()
    if (!id || !key) { setItems([]); return }
    let alive = true
    const made: string[] = []
    void Promise.all(key.split(' ').map(resource => fileUrl(token, `/api/class/visual/${encodeURIComponent(id)}/${encodeURIComponent(resource)}`)
      .then(url => { if (!alive) { URL.revokeObjectURL(url); return undefined } made.push(url); return { resource, url } })
      .catch(() => { if (alive) settle(resource, false); return undefined })))
      .then(list => { if (alive) setItems(list.filter(item => item !== undefined)) })
    return () => { alive = false; made.forEach(url => URL.revokeObjectURL(url)) }
  }, [token, id, key])
  return items.length ? <section className="visuals">{items.map(({ resource, url }) => <img key={url} src={url} alt="Jane 展示的图"
    onLoad={() => settle(resource, true)} onError={() => settle(resource, false)} />)}</section> : null
}

export default function Classroom({ token, onLeave }: { token: string, onLeave: () => void }) {
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
  const pictured = useRef(new Set<string>())
  // Study: Jane speaks first and her text appears only after the audio; a simulation is text only.
  const voiced = snapshot?.realm === 'study'
  const speaker = useRef<Speaker | null>(null)
  const presenting = useRef<Promise<void>>(Promise.resolve())
  const playing = useRef<string | null>(null)
  const deliveryIds = useRef(new Map<string, string>())
  const started = useRef(new Map<string, Promise<void>>())
  const latest = useRef(snapshot)
  latest.current = snapshot
  const halt = useRef(false)
  // Jane's sentence is fetched (synthesis can take seconds), then heard.
  const [voicing, setVoicing] = useState<'' | 'preparing' | 'speaking'>('')
  const [locked, setLocked] = useState(false)
  const microphone = useRef<Microphone | null>(null)
  const setCurrent = useCallback((value: Snapshot) => setSnapshot(old => old && old.core_revision > value.core_revision ? old : value), [])
  const localCancelled = presentationCancelled(cancellation, snapshot)
  // A pause typed or spoken as an answer reaches the core without the local cancel path.
  const halted = localCancelled || snapshot?.mode === 'paused'
  useEffect(() => {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const socket = new WebSocket(`${protocol}//${location.host}/api/class/events`, `project-j.${token}`)
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
    if (path === 'class/input') setLastInput((payload as TextInput).event_id)
    try {
      const value = await api<Ack>(token, path, payload)
      setCurrent(value.snapshot); setFailedRequest(undefined)
      return value
    }
    catch (error) {
      const failure = error as RequestFailure
      if (path === 'class/input' && !failure.unknown) setLastInput(previous)
      setFailedRequest(failure.unknown ? { path, payload } : undefined); setMessage(failure.message); return undefined
    }
    finally { setInFlight(count => count - 1) }
  }
  const control = async (command: Control['command']) => {
    const eventId = uid()
    setMessage('')
    if (command === 'stop' || command === 'pause') { halt.current = true; speaker.current?.stop() }
    if (command === 'stop' || command === 'pause') setCancellation(current => cancel(current, eventId, command))
    try {
      const value = await api<Snapshot>(token, 'class/control', { event_id: eventId, command })
      if (command === 'pause') setCancellation(current => acknowledgePause(current, eventId, value))
      setCurrent(value)
    } catch (error) {
      const failure = error as RequestFailure
      setMessage(command !== 'stop' && command !== 'pause' ? failure.message
        : failure.unknown ? '本端已停止呈现；核心回执未取得，可再按一次。' : `本端已停止呈现；${failure.message}`)
    }
  }
  const sendDelivery = (payload: DeliveryFeedback) => api<Snapshot>(token, 'class/delivery', payload).then(setCurrent).catch(error => {
    const failure = error as RequestFailure
    if (failure.unknown) setFailedDelivery(payload)
    setMessage(failure.unknown ? '呈现回执未取得；请接续原呈现回执。' : failure.message)
  })
  useEffect(() => {
    if (!voiced || speaker.current) return
    const created = new Speaker(token, (action, at) => {
      if (playing.current === action.action_id) setVoicing('speaking')
      const id = deliveryIds.current.get(action.action_id)
      if (id) started.current.set(id, sendDelivery(startedReport(action, id, at)))
    })
    speaker.current = created
    setLocked(created.locked)
    return () => { created.close(); speaker.current = null }
  }, [voiced, token])
  useEffect(() => () => { speaker.current?.stop(); microphone.current?.discard() }, [])
  useEffect(() => {
    if (snapshot && ['paused', 'offscreen', 'ended'].includes(snapshot.mode)) speaker.current?.stop()
  }, [snapshot])
  useEffect(() => {
    const action = snapshot?.pending_action
    if (!action || localCancelled || delivered.current.has(action.action_id)) return
    if (voiced) {
      const voice = speaker.current
      if (!voice) return
      delivered.current.add(action.action_id)
      const id = uid()
      deliveryIds.current.set(action.action_id, id)
      halt.current = false
      playing.current = action.action_id
      setVoicing('preparing')
      presenting.current = voice.play(action).then(async outcome => {
        if (playing.current === action.action_id) { playing.current = null; setVoicing('') }
        const now = latest.current
        const halted = halt.current || !now || ['paused', 'offscreen', 'ended'].includes(now.mode)
          || now.pending_action?.action_id !== action.action_id
        // In order: "started" first, and a text fallback before the student's input does.
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
  }, [snapshot, token, setCurrent, localCancelled, voiced, locked])
  // The student cutting in stops Jane and waits until the full text has been reported.
  const interrupt = async () => {
    if (!voiced) return
    speaker.current?.stop()
    await presenting.current
  }
  const input = async (value: string, intent: TextInput['intent'] = 'unknown', supplement = false) => {
    await interrupt()
    const result = await send('class/input', { event_id: uid(), text: value, intent,
      relation: supplement ? 'supplement' : 'new', related_event_id: supplement ? lastInput : null })
    // Keep anything typed while this request was being judged.
    if (result) setText(current => current === value ? '' : current)
  }
  const beginReading = async () => {
    await interrupt()
    if (voiced) {
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
    if (voiced) {
      try { audio = toBase64(microphone.current ? await microphone.current.finish() : new Uint8Array()) } catch {
        setMessage('录音无法读取，请重录。')
        return
      }
    }
    // Simulation convention: the recording bytes are the text the simulated student read.
    else audio = btoa(String.fromCharCode(...new TextEncoder().encode(readingText)))
    const result = await send('class/recording', { event_id: uid(), audio_base64: audio, ...timing })
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
  // Confirming Jane's goal or closing words is asked in a pop-up. Set aside, it stays a plain button.
  const confirmBox = useRef<HTMLDialogElement>(null)
  const [setAside, setSetAside] = useState('')
  const confirming = confirmationKey(snapshot)
  const asking = Boolean(confirming) && !halted && !busy && setAside !== confirming
  useEffect(() => {
    const box = confirmBox.current
    if (asking && !box?.open) box?.showModal()
    if (!asking && box?.open) box.close()
  }, [asking])
  const confirm = () => void input('听到了，收到当前说明。', 'confirmation')
  const progress = snapshot && classProgress(snapshot)
  const shown = !voiced ? snapshot?.pending_action ?? snapshot?.presented_action : locked || voicing ? undefined : snapshot?.presented_action
  // Jane left, the story in the middle, the student right (1:2:1), each scrolling on its own:
  // no page scroll between her question, the story and the answer box.
  return <main className="student classroom">
    <header className="class-bar">
      <div><span className="eyebrow">{voiced ? 'JANE · 课堂' : 'JANE · 模拟课堂'}</span><h1>和 Jane 上课</h1></div>
      {progress && <div className="class-progress"><span>{progress.label}</span><progress value={progress.value} max={progress.max} aria-label="课堂进度" /></div>}
      <div className="controls"><span>{snapshot ? stateLabels[snapshot.activity_state] : '正在连接原会话'}</span>
        {snapshot?.mode !== 'ended' && <div className="row">
          {snapshot?.mode === 'paused' ? <button onClick={() => void control('resume')}>继续</button>
            : <button className="quiet" disabled={halted} onClick={() => void control('pause')}>暂停</button>}
          <button className="danger" onClick={() => void control('stop')}>立即停止</button>
          <details className="more"><summary>更多</summary><div className="row">
            <button className="quiet" disabled={halted} onClick={() => void control('skip')}>跳过当前题</button>
            <button className="quiet" disabled={halted} onClick={() => void control('close')}>收尾</button></div></details></div>}</div>
    </header>
    <div className="stage">
      <div className="side">
        <section className="jane"><span className="avatar" aria-hidden="true">J</span><div><h2>Jane</h2>
          {localCancelled ? <p>呈现已取消。</p>
            : voiced && locked ? <p>请点“打开声音”，听 Jane 说话。</p>
            : voicing === 'preparing' ? <Progress label="Jane 正在准备要说的话……" />
            : voicing === 'speaking' ? <p aria-live="polite">Jane 正在说话……</p>
            : <p aria-live="polite">{shown?.text ?? '等待同一核心的下一动作。'}</p>}
          {!localCancelled && !voicing && (busy || snapshot?.mode === 'judging') && <Progress label="Jane 正在看你的回答……" />}
          {voiced && locked && <button onClick={() => void speaker.current?.resume().then(() => setLocked(false))}>打开声音</button>}
          {snapshot && snapshot.mode !== 'offscreen' && snapshot.mode !== 'ended' && <div className="row">
            <button className="quiet" disabled={busy || halted} onClick={() => void input('帮我一下', 'help')}>帮我一下</button>
            <button className="quiet" disabled={halted} onClick={() => void control('repeat')}>重复当前内容</button></div>}</div></section>
      </div>
      <div className="side text">
        {!localCancelled && <Visuals token={token} action={shown ?? undefined} onShown={(action, shown) => {
          if (pictured.current.has(action.action_id)) return  // Once per action, though it may show again.
          pictured.current.add(action.action_id)
          void sendDelivery(visualReport(action, shown, new Date()))
        }} />}
        {!localCancelled && Object.entries(snapshot?.materials ?? {}).map(([id, material]) => <section key={id} className="reading"><pre>{material}</pre></section>)}
        {!Object.keys(snapshot?.materials ?? {}).length && <p className="hint">课文会在这里显示。</p>}
      </div>
      <div className="side">
        {message && <div className="notice" role="alert">{message}</div>}
        {failedRequest && <button className="quiet" disabled={busy} onClick={() => void send(failedRequest.path, failedRequest.payload)}>接续原提交</button>}
        {failedDelivery && <button className="quiet" onClick={() => {
          const payload = failedDelivery
          void api<Snapshot>(token, 'class/delivery', payload).then(value => {
            setCurrent(value); setFailedDelivery(undefined)
          }).catch(error => {
            const failure = error as RequestFailure
            if (!failure.unknown) setFailedDelivery(undefined)
            setMessage(failure.message)
          })
        }}>接续原呈现回执</button>}
        {localCancelled && <p className="notice">本端已停止呈现。暂停后按“继续”恢复；停止后不能恢复。</p>}
        {reading && snapshot?.mode !== 'ended' && <section className="reading-aloud">
          <h2>朗读这一段</h2><p>{String(reading.reference_text)}</p>
          {!voiced && <label>模拟朗读内容（不使用麦克风，录音只交发音评分）<textarea value={readingText} onChange={event => setReadingText(event.target.value)} disabled={halted} /></label>}
          <p>{{ idle: '未开始', recording: '录音中', paused: '已暂停' }[recorder.state]}</p>
          <div className="row">
            {recorder.state === 'idle' && <button disabled={busy || halted} onClick={() => void beginReading()}>开始录音</button>}
            {recorder.state === 'recording' && <button className="quiet" onClick={() => { microphone.current?.pause(); setRecorder(pauseRecording(recorder, performance.now())) }}>暂停录音</button>}
            {recorder.state === 'paused' && <button className="quiet" onClick={() => { microphone.current?.resume(); setRecorder(resumeRecording(recorder, performance.now())) }}>继续录音</button>}
            {recorder.state !== 'idle' && <><button disabled={busy} onClick={() => void finishReading(performance.now())}>结束并提交</button>
              <button className="quiet" onClick={() => { microphone.current?.discard(); setRecorder(idle) }}>重录</button></>}
          </div></section>}
        {snapshot?.mode === 'offscreen' ? <section><p>离屏期间没有采集。回来后需要报告实际条件与结果，返回本身不算完成。</p><button onClick={() => void control('return')}>返回，报告结果</button></section> :
          snapshot?.mode !== 'ended' && <section><label>{voiced ? '你的回答或报告' : '你的回答或报告（模拟）'}<textarea value={text} onChange={event => setText(event.target.value)} disabled={localCancelled} /></label>
            <div className="row"><button disabled={busy || !text.trim() || halted || Boolean(reading)} onClick={() => void input(text)}>提交</button>
              {canConfirm(snapshot) && setAside === confirming && <button className="quiet" disabled={busy || halted} onClick={confirm}>确认收到说明</button>}
              <button className="quiet" disabled={!lastInput || !text.trim() || halted} onClick={() => void input(text, 'unknown', true)}>还没说完，补充</button></div></section>}
        {snapshot?.mode === 'ended' && <section><h2>本堂已结束</h2>{snapshot.end && <p>结束原因：{endLabels[String(snapshot.end.reason)] ?? String(snapshot.end.reason)}。</p>}<p>只保留已发生范围；课后评价会自动生成。</p><button onClick={onLeave}>回到主页</button></section>}
        <p className="hint">{voiced ? '先听 Jane 说，说完再显示文字；朗读录音只交本机评分，不保存。' : '文字模拟；不会录音或播放真实声音。'}</p>
      </div>
    </div>
    {/* Closed by the page while the confirmation is being sent; by the student (button or Esc) to set it aside. */}
    <dialog ref={confirmBox} className="confirm-box" onClose={() => { if (!busy) setSetAside(confirming) }}>
      <h2>{snapshot?.wait_kind === 'closing_confirmation' ? '本课小结' : '本课说明'}</h2>
      <p>{snapshot?.presented_action?.text}</p>
      {message && <div className="notice" role="alert">{message}</div>}
      <div className="row"><button onClick={confirm}>确认收到说明</button>
        <button className="quiet" onClick={() => confirmBox.current?.close()}>先不确认</button></div>
      <p className="hint">先不确认也可以提问、重听或停止；之后在作答区确认。</p>
    </dialog>
  </main>
}
