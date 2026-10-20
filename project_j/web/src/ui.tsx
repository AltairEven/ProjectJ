import { useRef, useState } from 'react'
import { show } from './api'
import type { Dashboard } from './client/types.gen'
import type { Target } from './next-step'
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
      setMessage(failure.detail && failure.detail !== failure.message ? `${failure.message}（${failure.detail}）` : failure.message)
    }
    finally { setBusy(false); setDoing('') }
  }
  const status = <>
    {message && <div className="notice" role="alert">{message}</div>}
    {busy && doing && <p className="notice" role="status">正在{doing}……</p>}
    {failed.current && <button className="quiet" disabled={busy} onClick={() => void run(failed.current!)}>接续原操作</button>}
  </>
  return { busy, run, setMessage, status }
}

export function Rows({ rows }: { rows: [string, unknown][] }) {
  return <dl className="rows">{rows.map(([label, value], index) =>
    <div key={index}><dt>{label}</dt><dd>{value == null || value === '' ? '无' : show(value)}</dd></div>)}</dl>
}
