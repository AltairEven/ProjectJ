// Jane's voice for an approved action: audio first, the text only after it (parent's decision).
import { decodePcm16 } from './audio.ts'
import type { AllowedAction, DeliveryFeedback } from './client/types.gen'

export type Outcome = { kind: 'completed' | 'failed' | 'interrupted'; started: boolean }

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

export class Speaker {
  private context = new AudioContext()
  private sources: AudioBufferSourceNode[] = []
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
    return new Promise(resolve => {
      let started = false, settled = false
      const settle = (kind: Outcome['kind']) => {
        if (settled) return
        settled = true
        if (this.abort === abort) this.abort = undefined
        resolve({ kind, started })
      }
      abort.signal.addEventListener('abort', () => settle('interrupted'))
      const begin = () => { started = true }
      this.stream(action, abort.signal, begin, settle).catch(() => settle(abort.signal.aborted ? 'interrupted' : 'failed'))
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
  }

  private async stream(action: AllowedAction, signal: AbortSignal, begin: () => void, settle: (kind: Outcome['kind']) => void) {
    if (this.locked) await new Promise<void>(release => this.waiting.push(release))
    if (signal.aborted) return
    const response = await fetch(`/api/student/speech/${encodeURIComponent(action.action_id)}`,
      { headers: { Authorization: `Bearer ${this.token}` }, signal })
    if (!response.ok || !response.body) return settle('failed')
    const rate = Number(/rate=(\d+)/.exec(response.headers.get('content-type') ?? '')?.[1] ?? 24000)
    const reader = response.body.getReader()
    let next = 0, carry: number | null = null, last: AudioBufferSourceNode | undefined
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      const decoded = decodePcm16(value, carry)
      carry = decoded.carry
      if (!decoded.samples.length) continue
      const buffer = this.context.createBuffer(1, decoded.samples.length, rate)
      buffer.copyToChannel(decoded.samples, 0)
      const source = this.context.createBufferSource()
      source.buffer = buffer
      source.connect(this.context.destination)
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
}
