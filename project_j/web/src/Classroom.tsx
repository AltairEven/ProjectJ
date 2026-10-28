import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fileUrl, uid } from './api'
import { acknowledgePause, cancel, presentationCancelled, type Cancellation } from './cancellation'
import { canConfirm, confirmationKey } from './confirmation'
import { endLabels } from './report'
import { finishRecording, idle, pauseRecording, recordedMs, resumeRecording, startRecording, type Recorder } from './recording'
import type { RequestFailure } from './request'
import { Speaker, pictureReport, reportsFor, startedReport, visualReport } from './speaker'
import { Microphone } from './microphone'
import { passage, pickWord, sentenceOf, wordTokens } from './passage'
import { VocabularyBox } from './VocabularyBox'
import type { Entry } from './vocabulary'
import { classProgress, taskMarks } from './progress'
import { Progress } from './ui'
import { answerMode, board, picturesSettled, type TaskView } from './tasks'
import { TaskInput } from './TaskInput'
import type { AllowedAction, Snapshot, Control, DeliveryFeedback, TextInput, InputResult as Ack } from './client/types.gen'

const stateLabels: Record<string, string> = { running: 'In class', offscreen: 'Away from the screen', paused: 'Paused', closing: 'Wrapping up', ended: 'Finished' }

// Class controls as icons (no icon library): the label is the tooltip and the accessible name.
const iconPaths: Record<string, string> = {
  pause: 'M6 4h4v16H6zM14 4h4v16h-4z',
  play: 'M7 4l12 8-12 8z',
  stop: 'M5 5h14v14H5z',
  skip: 'M5 4l10 8-10 8zM17 4h3v16h-3z',
  flag: 'M5 3h2v18H5zM7 4h11l-3 4 3 4H7z',
}
const questions = {
  stop: ['Stop the class now?', 'The class ends here. Only what happened is kept, and your evaluation follows by itself.'],
  skip: ['Skip this question?', 'It stays undone and Jane moves on to the next one.'],
  close: ['Wrap up the class?', 'Jane sums up what happened; the class ends after you confirm her summary.'],
} as const
function IconButton({ icon, label, className = '', ...rest }: { icon: string, label: string } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" className={`icon-button ${className}`.trim()} aria-label={label} title={label} {...rest}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d={iconPaths[icon]} /></svg></button>
}

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
  return items.length ? <section className="visuals">{items.map(({ resource, url }) => <img key={url} src={url} alt="A picture Jane shows"
    onLoad={() => settle(resource, true)} onError={() => settle(resource, false)} />)}</section> : null
}

// Story and option pictures, fetched by resource while the class offers them (ADR-0026). Each
// id ends displayed (true) or failed (false); the image's own load event settles a fetched one.
function usePictures(token: string, ids: string[]) {
  const [urls, setUrls] = useState<Record<string, string>>({})
  const [outcomes, setOutcomes] = useState<Record<string, boolean>>({})
  const key = ids.join(' ')
  useEffect(() => {
    setUrls({}); setOutcomes({})
    if (!key) return
    let alive = true
    const made: string[] = []
    for (const id of key.split(' ')) {
      fileUrl(token, `/api/class/picture/${encodeURIComponent(id)}`)
        .then(url => { if (!alive) { URL.revokeObjectURL(url); return } made.push(url); setUrls(old => ({ ...old, [id]: url })) })
        .catch(() => { if (alive) setOutcomes(old => ({ ...old, [id]: false })) })
    }
    return () => { alive = false; made.forEach(url => URL.revokeObjectURL(url)) }
  }, [token, key])
  const settle = useCallback((id: string, displayed: boolean) => setOutcomes(old => id in old ? old : { ...old, [id]: displayed }), [])
  return { urls, outcomes, settle }
}

// The one story word selected while reading, its sentence, and where its button goes; undefined
// for anything else (2026-10-28: select, then "Add to vocabulary", instead of tapping words).
function selectedWord(selection: Selection | null) {
  if (!selection || selection.isCollapsed || !selection.rangeCount) return undefined
  const range = selection.getRangeAt(0)
  const node = range.commonAncestorContainer
  const item = (node instanceof Element ? node : node.parentElement)?.closest('.reading li')
  const picked = pickWord(range.toString())
  if (!item || !picked) return undefined
  const before = document.createRange()
  before.setStart(item, 0)
  before.setEnd(range.startContainer, range.startOffset)
  const box = range.getBoundingClientRect()
  return { word: picked.word, sentence: sentenceOf(item.textContent ?? '', before.toString().length + picked.at), x: box.left, y: box.bottom }
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
  const asked = useRef<AllowedAction>(undefined)
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
    socket.onerror = () => setMessage('The connection is unclear. Stop still works; refreshing only rejoins this class.')
    return () => socket.close()
  }, [token, setCurrent])
  const send = async (path: string, payload: unknown) => {
    setInFlight(count => count + 1); setMessage('')
    const previous = lastInput
    // An answer's request returns only after judging; bind it now so "I'm not done" can follow it meanwhile.
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
        : failure.unknown ? 'Stopped on this page; no reply came back, so you can press it again.' : `Stopped on this page. ${failure.message}`)
    }
  }
  const sendDelivery = (payload: DeliveryFeedback) => api<Snapshot>(token, 'class/delivery', payload).then(setCurrent).catch(error => {
    const failure = error as RequestFailure
    if (failure.unknown) setFailedDelivery(payload)
    setMessage(failure.unknown ? 'The delivery receipt did not come back; send it again.' : failure.message)
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
        setMessage('Reading aloud needs the microphone. Allow it in the address bar, then try again.')
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
      try { audio = (microphone.current ? await microphone.current.finish() : new Uint8Array()).toBase64() } catch {
        setMessage('The recording could not be read. Please record again.')
        return
      }
    }
    // Simulation convention: the recording bytes are the text the simulated student read.
    else audio = new TextEncoder().encode(readingText).toBase64()
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
  // Stop, skip and wrap up each ask once before the command goes out (2026-10-25).
  const askBox = useRef<HTMLDialogElement>(null)
  const [checking, setChecking] = useState<keyof typeof questions | null>(null)
  const ask = (command: keyof typeof questions) => { setChecking(command); askBox.current?.showModal() }
  const [setAside, setSetAside] = useState('')
  const confirming = confirmationKey(snapshot)
  const asking = Boolean(confirming) && !halted && !busy && setAside !== confirming
  useEffect(() => {
    const box = confirmBox.current
    if (asking && !box?.open) box?.showModal()
    if (!asking && box?.open) box.close()
  }, [asking])
  const confirm = () => void input('Yes, I heard.', 'confirmation')
  // A clicked task is answered on the task itself: no text box, and a hint instead of a plea.
  const mode = answerMode(snapshot)
  const respond = async (payload: { intent: 'answer' | 'help', response?: Record<string, unknown> }) => {
    await interrupt()
    await send('class/response', { event_id: uid(), ...payload })
  }
  const progress = snapshot && classProgress(snapshot)
  // Words are marked only while reading; marking is never scored (design/learning).
  const vocabulary = (snapshot?.vocabulary ?? []) as unknown as Entry[]
  const tapping = mode === 'story' && snapshot?.wait_kind === 'reading' && !halted
  const markedWords = new Set(vocabulary.map(entry => String(entry.surface)))
  const [picked, setPicked] = useState<ReturnType<typeof selectedWord>>()
  useEffect(() => {
    if (!tapping) { setPicked(undefined); return }
    const update = () => setPicked(selectedWord(document.getSelection()))
    document.addEventListener('selectionchange', update)
    return () => document.removeEventListener('selectionchange', update)
  }, [tapping])
  const markWord = async (word: string, sentence: string) => {
    setMessage('')
    try { setCurrent(await api<Snapshot>(token, 'class/word', { event_id: uid(), word, sentence, marked: !markedWords.has(word.toLowerCase()) })) }
    catch (error) { setMessage((error as RequestFailure).message) }
  }
  const regenerateWord = async (surface: string) => {
    try { setCurrent(await api<Snapshot>(token, 'class/word/regenerate', { surface })) }
    catch (error) { setMessage((error as RequestFailure).message) }
  }
  // Marked words stay highlighted while reading; the text itself stays plain to select.
  const highlighted = (text: string) => wordTokens(text).map((token, index) =>
    token.word && markedWords.has(token.text.toLowerCase()) ? <mark key={index} className="marked">{token.text}</mark> : token.text)
  const shown = !voiced ? snapshot?.pending_action ?? snapshot?.presented_action : locked || voicing ? undefined : snapshot?.presented_action
  // The question stays on Jane's board through hints and feedback; hints refresh the line under it.
  if (shown?.kind === 'question') asked.current = shown
  const posed = board(asked.current, shown ?? undefined, snapshot?.pending_action ?? undefined, snapshot?.task_instance_id)
  // Story pictures beside their paragraphs; picture options of the clicked question (ADR-0026).
  const illustrations = (snapshot?.illustrations ?? []) as { resource_id: string, paragraph: number }[]
  const storyIds = illustrations.map(item => item.resource_id)
  const story = usePictures(token, storyIds)
  const task = snapshot?.task as TaskView | undefined
  const optionIds = task?.type === 'choice' ? task.pictures ?? [] : []
  const options = usePictures(token, optionIds)
  const optionState = picturesSettled(optionIds.map(id => options.outcomes[id]))
  // Each part is reported once per action it showed with, once every picture has an outcome.
  const report = (part: 'story' | 'options', ids: string[], outcomes: Record<string, boolean>, settled = ids.every(id => id in outcomes)) => {
    if (!shown || !ids.length || !settled) return
    const key = `${shown.action_id}:${part}`
    if (pictured.current.has(key)) return
    pictured.current.add(key)
    void sendDelivery(pictureReport(shown, part, ids, ids.filter(id => outcomes[id]), new Date()))
  }
  useEffect(() => report('story', storyIds, story.outcomes))
  // Words take over at the first failure: the pictures not yet shown never will be.
  useEffect(() => report('options', optionIds, options.outcomes, optionState !== 'loading'))
  const [zoom, setZoom] = useState('')
  const zoomBox = useRef<HTMLDialogElement>(null)
  // The enlarged picture closes with its story; closing clears it.
  useEffect(() => { if (!storyIds.length) zoomBox.current?.close() }, [storyIds.length])
  const figure = (paragraph: number) => {
    const item = illustrations.find(entry => entry.paragraph === paragraph)
    const url = item && story.urls[item.resource_id]
    if (!item || !url || story.outcomes[item.resource_id] === false) return null
    return <figure className="illustration"><button type="button" className="picture" aria-label="Enlarge the picture" onClick={() => { setZoom(url); zoomBox.current?.showModal() }}>
      <img src={url} alt="A picture for this part of the story" onLoad={() => story.settle(item.resource_id, true)}
        onError={() => story.settle(item.resource_id, false)} /></button></figure>
  }
  // Jane's board left, the textbook page in the middle, the student's notebook right (1:2:1), each
  // scrolling on its own: no page scroll between her question, the story and the answer box.
  return <main className="student classroom">
    <header className="class-bar">
      {snapshot?.mode !== 'ended' && <div className="row class-controls">
        <IconButton icon="stop" label="Stop now" className="danger" onClick={() => ask('stop')} />
        {snapshot?.mode === 'paused' ? <IconButton icon="play" label="Resume" onClick={() => void control('resume')} />
          : <IconButton icon="pause" label="Pause" className="quiet" disabled={halted} onClick={() => void control('pause')} />}</div>}
      <h1>Class with Jane{!voiced && <span className="tag">Simulation</span>}</h1>
      {progress && snapshot && <div className="class-progress"><span>{progress.label}</span>
        <progress className="sr-only" value={progress.value} max={progress.max} aria-label="Class progress" />
        <span className="ticks" aria-hidden="true">{taskMarks(snapshot.task_index, snapshot.task_count).map((mark, index) => <i key={index} className={mark} />)}</span>
        {snapshot.mode !== 'ended' && <span className="row">
          <IconButton icon="skip" label="Skip this question" className="quiet" disabled={halted || snapshot.task_index < 0} onClick={() => ask('skip')} />
          <IconButton icon="flag" label="Wrap up" className="quiet" disabled={halted} onClick={() => ask('close')} /></span>}</div>}
      <div className="controls"><span>{snapshot ? stateLabels[snapshot.activity_state] : 'Joining the class'}</span></div>
    </header>
    <div className="stage">
      <div className="side board">
        <section className="jane"><span className="avatar" aria-hidden="true">J</span><div><h2>Jane</h2>
          {!localCancelled && posed && <p className="said" key={asked.current?.action_id}>{posed.question}</p>}
          {localCancelled ? <p>Stopped on this page.</p>
            : voiced && locked ? <p>Press "Turn on sound" to hear Jane.</p>
            : voicing === 'preparing' ? <Progress label="Jane is getting ready to speak…" />
            : voicing === 'speaking' ? <p aria-live="polite">Jane is speaking…</p>
            : posed ? posed.note && <p aria-live="polite" className="note said" key={shown?.action_id}>{posed.note}</p>
            : <p aria-live="polite" className="said" key={shown?.action_id}>{shown?.text ?? 'Waiting for Jane.'}</p>}
          {!localCancelled && !voicing && (busy || snapshot?.mode === 'judging') && <Progress label="Jane is reading your answer…" />}
          {voiced && locked && <button onClick={() => void speaker.current?.resume().then(() => setLocked(false))}>Turn on sound</button>}
          {snapshot && snapshot.mode !== 'offscreen' && snapshot.mode !== 'ended' && <div className="row">
            {mode !== 'task' && mode !== 'story' && <button className="quiet" disabled={busy || halted} onClick={() => void input('Help me, please.', 'help')}>Help me</button>}
            <button className="quiet" disabled={halted} onClick={() => void control('repeat')}>Say it again</button></div>}</div></section>
        {/* The words marked while reading, below Jane: meanings arrive after "I've finished reading". */}
        {!localCancelled && snapshot && snapshot.task_index >= 0 && <VocabularyBox token={token} entries={vocabulary} busy={busy}
          audioUrl={lemma => `/api/class/word-audio/${encodeURIComponent(lemma)}`} onRegenerate={surface => void regenerateWord(surface)} />}
      </div>
      <div className="side book" onScroll={() => setPicked(undefined)}>
        {/* The question's own picture stays through hints that bring none. */}
        {!localCancelled && <Visuals token={token} action={(posed && !shown?.visuals?.length ? asked.current : shown) ?? undefined} onShown={(action, shown) => {
          if (pictured.current.has(action.action_id)) return  // Once per action, though it may show again.
          pictured.current.add(action.action_id)
          void sendDelivery(visualReport(action, shown, new Date()))
        }} />}
        {!localCancelled && Object.entries(snapshot?.materials ?? {}).map(([id, material]) => {
          const { title, paragraphs } = passage(material)
          return <section key={id} className="reading">{title && <h2>{title}</h2>}
            {tapping && <p className="hint">Select a word you do not know, then press "Add to vocabulary".</p>}
            <ol>{paragraphs.map((text, index) => <li key={index}>{figure(index + 1)}{tapping ? highlighted(text) : text}</li>)}</ol></section>
        })}
        {!Object.keys(snapshot?.materials ?? {}).length && <p className="hint">{snapshot?.wait_kind === 'reading_start' ? 'The story shows when you press Start reading.' : 'The story shows here.'}</p>}
      </div>
      <div className="side notebook">
        {message && <div className="notice" role="alert">{message}</div>}
        {failedRequest && <button className="quiet" disabled={busy} onClick={() => void send(failedRequest.path, failedRequest.payload)}>Send it again</button>}
        {failedDelivery && <button className="quiet" onClick={() => {
          const payload = failedDelivery
          void api<Snapshot>(token, 'class/delivery', payload).then(value => {
            setCurrent(value); setFailedDelivery(undefined)
          }).catch(error => {
            const failure = error as RequestFailure
            if (!failure.unknown) setFailedDelivery(undefined)
            setMessage(failure.message)
          })
        }}>Send the receipt again</button>}
        {localCancelled && <p className="notice">Stopped on this page. After Pause, press Resume to go on; after Stop, the class cannot go on.</p>}
        {reading && snapshot?.mode !== 'ended' && <section className="reading-aloud">
          <h2>Read this part aloud</h2><p>{String(reading.reference_text)}</p>
          {!voiced && <label>Simulated reading (no microphone; it only goes to scoring)<textarea value={readingText} onChange={event => setReadingText(event.target.value)} disabled={halted} /></label>}
          <p>{{ idle: 'Not started', recording: 'Recording', paused: 'Paused' }[recorder.state]}</p>
          <div className="row">
            {recorder.state === 'idle' && <button disabled={busy || halted} onClick={() => void beginReading()}>Start recording</button>}
            {recorder.state === 'recording' && <button className="quiet" onClick={() => { microphone.current?.pause(); setRecorder(pauseRecording(recorder, performance.now())) }}>Pause recording</button>}
            {recorder.state === 'paused' && <button className="quiet" onClick={() => { microphone.current?.resume(); setRecorder(resumeRecording(recorder, performance.now())) }}>Resume recording</button>}
            {recorder.state !== 'idle' && <><button disabled={busy} onClick={() => void finishReading(performance.now())}>Finish and submit</button>
              <button className="quiet" onClick={() => { microphone.current?.discard(); setRecorder(idle) }}>Record again</button></>}
          </div></section>}
        {mode === 'task' && snapshot ? <TaskInput key={snapshot.task_instance_id} task={snapshot.task as TaskView} disabled={busy || halted}
          pictures={optionIds.length ? { urls: optionIds.map(id => options.urls[id]), state: optionState, onLoad: (index, displayed) => options.settle(optionIds[index], displayed) } : undefined}
          onSubmit={response => void respond({ intent: 'answer', response })} onHint={() => void respond({ intent: 'help' })} /> :
          // Reading the story alone, timed by the class (ADR-0022); no seconds are shown.
          mode === 'story' && snapshot ? (snapshot.wait_kind === 'reading_start'
            ? <section className="story-reading"><p>Read the story on your own. Take your time.</p>
              <button disabled={snapshot.mode !== 'waiting_input' || busy || halted} onClick={() => void control('start_reading')}>Start reading</button></section>
            : <section className="story-reading"><p>{snapshot.mode === 'paused' ? 'Reading paused.' : 'Timing your reading.'}</p>
              <button disabled={halted} onClick={() => void control('finish_reading')}>I've finished reading</button></section>) :
          snapshot?.mode === 'offscreen' ? <section><p>Nothing is recorded while you are away. When you come back, tell Jane what you did and what happened; coming back alone does not finish it.</p><button onClick={() => void control('return')}>I'm back: report</button></section> :
          // Free text only when the class waits for words; a read-aloud task has its recording section alone.
          mode === 'text' && snapshot && <section><label>{voiced ? 'Your answer or report' : 'Your answer or report (simulation)'}<textarea value={text} onChange={event => setText(event.target.value)} disabled={localCancelled} /></label>
            <div className="row"><button disabled={busy || !text.trim() || halted || Boolean(reading)} onClick={() => void input(text)}>Submit</button>
              {canConfirm(snapshot) && setAside === confirming && <button className="quiet" disabled={busy || halted} onClick={confirm}>Yes, I heard</button>}
              <button className="quiet" disabled={!lastInput || !text.trim() || halted} onClick={() => void input(text, 'unknown', true)}>I'm not done: add more</button></div></section>}
        {snapshot?.mode === 'ended' && <section><h2>The class is over</h2>{snapshot.end && <p>How it ended: {endLabels[String(snapshot.end.reason)] ?? String(snapshot.end.reason)}.</p>}<p>Only what happened is kept; your evaluation comes next by itself.</p><button onClick={onLeave}>Back to home</button></section>}
        <p className="hint">{voiced ? 'Listen to Jane first; her words show after she speaks. Reading aloud is scored on this computer and not saved.' : 'Text simulation; nothing is recorded or played.'}</p>
      </div>
    </div>
    {/* Pressing it keeps the selection: the press itself must not clear what it acts on. */}
    {picked && <button type="button" className="word-pop" style={{ left: picked.x, top: picked.y + 6 }} onMouseDown={event => event.preventDefault()}
      onClick={() => { void markWord(picked.word, picked.sentence); document.getSelection()?.removeAllRanges() }}>
      {markedWords.has(picked.word.toLowerCase()) ? 'Remove from vocabulary' : 'Add to vocabulary'}</button>}
    <dialog ref={zoomBox} className="zoom" onClose={() => setZoom('')} onClick={() => zoomBox.current?.close()}>
      {zoom && <img src={zoom} alt="The story picture, enlarged" />}</dialog>
    <dialog ref={askBox} className="confirm-box" onClose={() => setChecking(null)}>
      {checking && <><h2>{questions[checking][0]}</h2><p>{questions[checking][1]}</p>
        <div className="row"><button className={checking === 'stop' ? 'danger' : undefined} onClick={() => { const command = checking; askBox.current?.close(); void control(command) }}>Yes</button>
          <button className="quiet" onClick={() => askBox.current?.close()}>No</button></div></>}
    </dialog>
    {/* Closed by the page while the confirmation is being sent; by the student (button or Esc) to set it aside. */}
    <dialog ref={confirmBox} className="confirm-box" onClose={() => { if (!busy) setSetAside(confirming) }}>
      <h2>Summary</h2>
      <p>{snapshot?.presented_action?.text}</p>
      {message && <div className="notice" role="alert">{message}</div>}
      <div className="row"><button onClick={confirm}>Yes, I heard</button>
        <button className="quiet" onClick={() => confirmBox.current?.close()}>Not yet</button></div>
      <p className="hint">You can still ask, listen again or stop; confirm later in the answer area.</p>
    </dialog>
  </main>
}
