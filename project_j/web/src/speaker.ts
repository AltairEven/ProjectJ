// Jane's voice for an approved action: audio first, the text only after it (parent's decision).
import { decodePcm16, encodeWav } from './audio.ts'
import type { AllowedAction, DeliveryFeedback } from './client/types.gen'

export type Outcome = { kind: 'completed' | 'failed' | 'interrupted'; started: boolean }

// Chrome's cumulative output statistics for an audio context; other browsers have none.
type Stats = { underrunEvents: number; underrunDuration: number; averageLatency: number }
type Counts = { player: 'webaudio' | 'element'; rate?: number; reads: number; late: number; before?: Stats }
const statsOf = (context: AudioContext): Stats | undefined => {
  const live = (context as AudioContext & { playbackStats?: Stats }).playbackStats
  return live && { underrunEvents: live.underrunEvents, underrunDuration: live.underrunDuration, averageLatency: live.averageLatency }
}

// How one sentence was played, for the service log: no text and no audio. Statistics count only
// when taken before it too; a whole recording plays outside the audio context and has none.
export function playbackOf(sampleRate: number, counts: Counts, after: Stats | undefined) {
  const before = counts.before, both = before && after
  return {
    player: counts.player, sample_rate: sampleRate, reads: counts.reads, late: counts.late,
    underruns: both ? after.underrunEvents - before.underrunEvents : null,
    underrun_ms: both ? Math.round((after.underrunDuration - before.underrunDuration) * 1e4) / 10 : null,
    latency_ms: both ? Math.round(after.averageLatency * 1e4) / 10 : null,
  }
}

type Status = DeliveryFeedback['status']

const audio = (action: AllowedAction, deliveryId: string, sequence: number, status: Status, range: string[], at: Date): DeliveryFeedback => ({
  delivery_event_id: `${deliveryId}:${status}`, delivery_id: deliveryId, action_id: action.action_id,
  activity_id: action.activity_id, task_instance_id: action.task_instance_id, sequence,
  output_device_id: 'browser-speaker', modality: 'audio', status, delivered_range: range, occurred_at: at.toISOString(),
})

export function startedReport(action: AllowedAction, deliveryId: string, occurredAt: Date): DeliveryFeedback {
  return audio(action, deliveryId, 1, 'started', ['audio'], occurredAt)
}

// What to report once a sentence's audio ends, in sending order. When the child cut in or the
// audio failed, the full text is shown and reported first so the core waits for input; the audio
// outcome then only adds to the record. Halted sentences (pause, stop, replaced) show no text.
export function reportsFor(action: AllowedAction, outcome: Outcome, halted: boolean, deliveryId: string, now: Date): DeliveryFeedback[] {
  const sequence = outcome.started ? 2 : 1
  const partial = outcome.started ? ['audio_partial'] : []
  if (outcome.kind === 'completed') return [audio(action, deliveryId, 2, 'completed', ['audio'], now)]
  if (halted) return [outcome.started ? audio(action, deliveryId, 2, 'interrupted', partial, now) : audio(action, deliveryId, 1, 'cancelled', [], now)]
  const text: DeliveryFeedback = {
    delivery_event_id: `${deliveryId}:text`, delivery_id: `${deliveryId}:text`, action_id: action.action_id,
    activity_id: action.activity_id, task_instance_id: action.task_instance_id, sequence: 1,
    output_device_id: 'browser-text', modality: 'text', status: 'completed', delivered_range: ['text_content'], occurred_at: now.toISOString(),
  }
  return [text, audio(action, deliveryId, sequence, outcome.kind, partial, now)]
}

// Which of an action's pictures or animations actually displayed; a fact, not a transition.
export function visualReport(action: AllowedAction, shown: string[], now: Date): DeliveryFeedback {
  const status = shown.length === (action.visuals ?? []).length ? 'completed' : shown.length ? 'partial' : 'failed'
  return { delivery_event_id: `${action.action_id}:visual`, delivery_id: `${action.action_id}:visual`, action_id: action.action_id,
    activity_id: action.activity_id, task_instance_id: action.task_instance_id, sequence: 1, output_device_id: 'browser-screen',
    modality: 'image', status, delivered_range: shown, occurred_at: now.toISOString() }
}

export class Speaker {
  private context = new AudioContext()
  private sources: AudioBufferSourceNode[] = []
  private element?: HTMLAudioElement
  private abort?: AbortController
  private waiting: (() => void)[] = []
  private token: string
  private onStarted: (action: AllowedAction, occurredAt: Date) => void

  constructor(token: string, onStarted: (action: AllowedAction, occurredAt: Date) => void) {
    this.token = token
    this.onStarted = onStarted
  }

  // Browsers keep audio suspended until the page has been clicked.
  get locked() { return this.context.state !== 'running' }

  async resume() {
    await this.context.resume()
    for (const release of this.waiting.splice(0)) release()
  }

  play(action: AllowedAction): Promise<Outcome> {
    this.stop()
    const abort = new AbortController()
    this.abort = abort
    const counts: Counts = { player: 'webaudio', reads: 0, late: 0, before: statsOf(this.context) }
    return new Promise(resolve => {
      let started = false, settled = false
      const settle = (kind: Outcome['kind']) => {
        if (settled) return
        settled = true
        if (this.abort === abort) this.abort = undefined
        if (counts.reads) this.report(action, kind, counts)
        resolve({ kind, started })
      }
      abort.signal.addEventListener('abort', () => settle('interrupted'))
      const begin = () => { started = true }
      this.stream(action, abort.signal, begin, settle, counts).catch(() => settle(abort.signal.aborted ? 'interrupted' : 'failed'))
    })
  }

  stop() {
    this.abort?.abort()
    this.abort = undefined
    for (const source of this.sources) {
      source.onended = null
      source.stop()
    }
    this.sources = []
    this.release()
  }

  private release() {
    if (!this.element) return
    this.element.onended = this.element.onerror = null
    this.element.pause()
    URL.revokeObjectURL(this.element.src)
    this.element = undefined
  }

  // Releases the audio device when the page goes away.
  close() {
    this.stop()
    void this.context.close()
  }

  // Diagnosis only: a lost report changes nothing in class.
  private report(action: AllowedAction, outcome: Outcome['kind'], counts: Counts) {
    const body = { action_id: action.action_id, outcome, ...playbackOf(counts.rate ?? this.context.sampleRate, counts, statsOf(this.context)) }
    void fetch('/api/student/playback', { method: 'POST', body: JSON.stringify(body),
      headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json' } }).catch(() => undefined)
  }

  private async stream(action: AllowedAction, signal: AbortSignal, begin: () => void, settle: (kind: Outcome['kind']) => void, counts: Counts) {
    if (this.locked) await new Promise<void>(release => this.waiting.push(release))
    if (signal.aborted) return
    const response = await fetch(`/api/student/speech/${encodeURIComponent(action.action_id)}`,
      { headers: { Authorization: `Bearer ${this.token}` }, signal })
    if (!response.ok || !response.body) return settle('failed')
    const rate = Number(/rate=(\d+)/.exec(response.headers.get('content-type') ?? '')?.[1] ?? 24000)
    if (response.headers.get('x-speech-source') === 'recorded') return this.playWhole(action, response, rate, begin, settle, counts)
    const reader = response.body.getReader()
    let next = 0, carry: number | null = null, last: AudioBufferSourceNode | undefined
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      counts.reads++
      const decoded = decodePcm16(value, carry)
      carry = decoded.carry
      if (!decoded.samples.length) continue
      const buffer = this.context.createBuffer(1, decoded.samples.length, rate)
      buffer.copyToChannel(decoded.samples, 0)
      const source = this.context.createBufferSource()
      source.buffer = buffer
      source.connect(this.context.destination)
      if (last && next < this.context.currentTime) counts.late++
      const at = Math.max(next, this.context.currentTime)
      if (!last) {
        begin()
        const lead = at - this.context.currentTime + (this.context.outputLatency || 0)
        this.onStarted(action, new Date(Date.now() + lead * 1000))
      }
      source.start(at)
      next = at + buffer.duration
      this.sources.push(source)
      last = source
    }
    if (!last) return settle('failed')
    last.onended = () => {
      this.sources = []
      settle('completed')
    }
  }

  // An adopted recording arrives whole. Streamed in pieces it was heard with hiss in class;
  // played as one file through the media element, as in the workbench preview, it never was.
  private async playWhole(action: AllowedAction, response: Response, rate: number, begin: () => void, settle: (kind: Outcome['kind']) => void, counts: Counts) {
    const pcm = new Uint8Array(await response.arrayBuffer())
    Object.assign(counts, { player: 'element', rate, reads: 1, before: undefined })
    const element = new Audio(URL.createObjectURL(new Blob([encodeWav(decodePcm16(pcm, null).samples, rate)], { type: 'audio/wav' })))
    this.element = element
    element.onplaying = () => {
      element.onplaying = null
      begin()
      this.onStarted(action, new Date(Date.now() + (this.context.outputLatency || 0) * 1000))
    }
    element.onended = () => { this.release(); settle('completed') }
    element.onerror = () => { this.release(); settle('failed') }
    await element.play()
  }
}
