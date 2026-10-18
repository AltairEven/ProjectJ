// A reading segment's recording: one take, pauses included, ended by the child or the maximum.
export type Recorder =
  | { state: 'idle' }
  | { state: 'recording' | 'paused'; startedAt: number; segmentStart: number; elapsed: number; pauses: number }

export const idle: Recorder = { state: 'idle' }

export function startRecording(r: Recorder, now: number): Recorder {
  return r.state === 'idle' ? { state: 'recording', startedAt: now, segmentStart: now, elapsed: 0, pauses: 0 } : r
}

export function pauseRecording(r: Recorder, now: number): Recorder {
  return r.state === 'recording' ? { ...r, state: 'paused', elapsed: r.elapsed + now - r.segmentStart, pauses: r.pauses + 1 } : r
}

export function resumeRecording(r: Recorder, now: number): Recorder {
  return r.state === 'paused' ? { ...r, state: 'recording', segmentStart: now } : r
}

export function recordedMs(r: Recorder, now: number): number {
  if (r.state === 'idle') return 0
  return r.state === 'recording' ? r.elapsed + now - r.segmentStart : r.elapsed
}

export function finishRecording(r: Recorder, now: number, maxSeconds: number) {
  if (r.state === 'idle') return undefined
  const recording_ms = Math.min(recordedMs(r, now), maxSeconds * 1000)
  return { input_started_ms: r.startedAt, input_ended_ms: now, recording_ms, pause_count: r.pauses,
    completion_trigger: recording_ms >= maxSeconds * 1000 ? 'max_duration' as const : 'explicit' as const }
}
