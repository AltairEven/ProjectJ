import { useRef, useState } from 'react'
import { show } from './api'
import type { Dashboard, JobRow } from './client/types.gen'
import { jobLines, type Target } from './next-step'
import { jobProgress } from './progress'
import type { RequestFailure } from './request'

export type PageProps = {
  token: string, dashboard: Dashboard, learner: string, detail?: string,
  go: (page: Target, detail?: string) => void, refresh: () => Promise<void>,
}

/** One operation at a time per page; an unknown outcome can resume the same request. */
export function useRun(refresh: () => Promise<void>) {
  const [busy, setBusy] = useState(false)
  const [doing, setDoing] = useState('')
  const [message, setMessage] = useState('')
  const failed = useRef<(() => Promise<void>) | null>(null)
  const run = async (operation: () => Promise<void>, label = '') => {
    setBusy(true); setDoing(label); setMessage('')
    try { await operation(); failed.current = null; await refresh() }
    catch (error) {
      const failure = error as RequestFailure
      failed.current = failure.unknown ? operation : null
      setMessage(failure.detail && failure.detail !== failure.message ? `${failure.message} (${failure.detail})` : failure.message)
    }
    finally { setBusy(false); setDoing('') }
  }
  const status = <>
    {message && <div className="notice" role="alert">{message}</div>}
    {busy && doing && <Progress label={`${doing}…`} />}
    {failed.current && <button className="quiet" disabled={busy} onClick={() => void run(failed.current!)}>Try the same action again</button>}
  </>
  return { busy, run, setMessage, status }
}

export function Rows({ rows }: { rows: [string, unknown][] }) {
  return <dl className="rows">{rows.map(([label, value], index) =>
    <div key={index}><dt>{label}</dt><dd>{value == null || value === '' ? 'None' : show(value)}</dd></div>)}</dl>
}

/** A wait the student can see: the bar fills when the amount is known, otherwise it keeps moving. */
export function Progress({ label, value, max }: { label: string, value?: number, max?: number }) {
  return <p className="notice progress" role="status">{label}<progress value={value} max={max} aria-label={label} /></p>
}

export function JobLine({ job }: { job: JobRow }) {
  const line = jobLines([job])[0]
  return job.state === 'failed' ? <p className="notice">{line}</p> : <Progress label={line} {...jobProgress(job.progress)} />
}
