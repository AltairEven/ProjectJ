import { useState } from 'react'
import { api, uid } from '../api'
import { dateTimeText } from '../dates'
import { modelLines } from '../report'
import { Rows, useRun, type PageProps } from '../ui'

export default function Settings({ token, dashboard, learner, refresh }: PageProps) {
  const { busy, run, setMessage, status } = useRun(refresh)
  const [backupId, setBackupId] = useState('')
  const [restored, setRestored] = useState<Record<string, unknown>>()
  const [lostDrafts, setLostDrafts] = useState<Record<string, string>>({})
  const mode = dashboard.realm === 'study' ? 'study' : 'simulation'
  const pendingSeals = dashboard.pending_seals ?? []
  const sealErrors = Object.keys(dashboard.seal_errors ?? {})
  const exportRecords = () => { const requestId = uid(); void run(async () => {
    const data = await api<{ path: string }>(token, 'export', { request_id: requestId, learner_id: learner })
    const response = await fetch(`/api/export-file?path=${encodeURIComponent(data.path)}`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' })
    if (!response.ok) throw new Error('The export did not come back')
    const url = URL.createObjectURL(await response.blob()); const anchor = document.createElement('a')
    anchor.href = url; anchor.download = `project-j-${dashboard.realm}.json`; anchor.click(); URL.revokeObjectURL(url)
  }, 'Exporting') }
  return <>
    {status}
    <section><h2>Model and this computer</h2>
      <p>Data space: {mode}. {dashboard.verification?.result === 'pass' ? 'The checks match this build.' : 'The checks for this build are missing.'}</p>
      {modelLines(dashboard.models as Record<string, any> | undefined).map(line => <p key={line}>{line}</p>)}</section>
    <section><h2>Classes that stopped suddenly</h2>
      {pendingSeals.length > 0 && <p className="notice">The end of a class is not confirmed as saved yet. Check and finish saving it; do not start a new class to make up for it.</p>}
      {(pendingSeals.length > 0 || sealErrors.length > 0) &&
        <button disabled={busy} onClick={() => void run(async () => { await api(token, 'retry-seal', {}) }, 'Finishing the save')}>Check and finish saving</button>}
      {dashboard.lost_activities?.map(item => { const id = String(item.activity_id); const reports = (item.reports ?? []) as { text: string, reported_at: string }[]
        return <div className="notice" key={id}><p>Class started {dateTimeText(String(item.requested_at))}: {String(item.state)}</p>
          {reports.map(report => <p key={report.reported_at}>Note ({dateTimeText(report.reported_at)}): {report.text}</p>)}
          <label>Add what you know (saved as a new source; gaps stay and it does not go into an evaluation)<textarea value={lostDrafts[id] ?? ''} onChange={event => setLostDrafts(drafts => ({ ...drafts, [id]: event.target.value }))} /></label>
          <button className="quiet" disabled={busy || !lostDrafts[id]?.trim()} onClick={() => { const requestId = uid(); void run(async () => {
            await api(token, 'lost-report', { request_id: requestId, learner_id: learner, activity_id: id, text: lostDrafts[id] })
            setLostDrafts(drafts => ({ ...drafts, [id]: '' })); setMessage('Your note is saved; gaps stay and no evaluation was run.')
          }) }}>Save note</button></div> })}
      {!pendingSeals.length && !sealErrors.length && !dashboard.lost_activities?.length && <p>No classes stopped suddenly.</p>}</section>
    <section><h2>Backup and export</h2><div className="row">
      <button className="quiet" disabled={busy} onClick={exportRecords}>Export {mode} records</button>
      <button className="quiet" disabled={busy} onClick={() => void run(async () => { const data = await api<{ backup_id: string }>(token, 'backup', {}); setBackupId(data.backup_id); setMessage('The database and fixed materials are backed up.') }, 'Backing up')}>Back up</button>
      <button className="quiet" disabled={busy || !backupId} onClick={() => { const newRun = `restored-${uid()}`; void run(async () => setRestored(await api(token, 'restore', { backup_id: backupId, new_run_id: newRun })), 'Restoring') }}>Restore to a new folder</button></div>
      {restored && <Rows rows={Object.entries(restored)} />}</section>
  </>
}
