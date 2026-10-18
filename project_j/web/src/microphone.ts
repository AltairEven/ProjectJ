// One reading take from the browser microphone: kept in memory, sent once as 16 kHz mono WAV.
import { encodeWav, toMono } from './audio.ts'

export class Microphone {
  private stream?: MediaStream
  private recorder?: MediaRecorder
  private chunks: Blob[] = []

  async start() {
    this.discard()
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } })
    this.recorder = new MediaRecorder(this.stream)
    this.recorder.ondataavailable = event => { if (event.data.size) this.chunks.push(event.data) }
    this.recorder.start()
  }

  pause() { if (this.recorder?.state === 'recording') this.recorder.pause() }

  resume() { if (this.recorder?.state === 'paused') this.recorder.resume() }

  // The browser decodes and resamples to 16 kHz; an empty take stays empty (scored unknown).
  async finish(): Promise<Uint8Array> {
    const recorder = this.recorder
    if (!recorder) return new Uint8Array()
    const stopped = new Promise<void>(resolve => { recorder.onstop = () => resolve() })
    recorder.stop()
    await stopped
    const blob = new Blob(this.chunks, { type: recorder.mimeType })
    this.release()
    if (!blob.size) return new Uint8Array()
    const context = new AudioContext({ sampleRate: 16000 })
    try {
      const decoded = await context.decodeAudioData(await blob.arrayBuffer())
      const channels = Array.from({ length: decoded.numberOfChannels }, (_, i) => decoded.getChannelData(i))
      return encodeWav(toMono(channels), 16000)
    } finally {
      void context.close()
    }
  }

  discard() {
    if (this.recorder && this.recorder.state !== 'inactive') {
      this.recorder.onstop = null
      this.recorder.stop()
    }
    this.release()
  }

  private release() {
    this.stream?.getTracks().forEach(track => track.stop())
    this.stream = undefined
    this.recorder = undefined
    this.chunks = []
  }
}
