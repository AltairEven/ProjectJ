import { useState } from 'react'
import { api, uid } from '../api'
import { dateTimeText } from '../dates'
import { modelLines } from '../report'
import { EFFORTS, TIERS, bindingRows, deadlineLabels, probeNotes, type BindingRow } from '../settings'
import { Rows, useRun, type PageProps } from '../ui'

export default function Settings({ token, dashboard, learner, refresh }: PageProps) {
  const { busy, run, setMessage, status } = useRun(refresh)
  const [backupId, setBackupId] = useState('')
  const [restored, setRestored] = useState<Record<string, unknown>>()
  const [lostDrafts, setLostDrafts] = useState<Record<string, string>>({})
  const models = dashboard.models as Record<string, any> | undefined
  const inClass = !!dashboard.active
  const [deadlines, setDeadlines] = useState<Record<string, number>>(() => ({ ...(models?.deadlines ?? {}) }))
  const [rows, setRows] = useState<BindingRow[]>(() => bindingRows(models))
  const [probedClassroom, setProbedClassroom] = useState(false)
  const setRow = (index: number, field: keyof BindingRow, value: string) =>
    setRows(current => current.map((row, i) => i === index ? { ...row, [field]: value } : row))
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
      {modelLines(models).map(line => <p key={line}>{line}</p>)}
      {probeNotes(models, probedClassroom).map(line => <p className="notice" key={line}>{line}</p>)}</section>
    {models?.configurable && <section><h2>Waiting time</h2>
      <p>Seconds each kind of AI call may take; saved now, used by the next call, also during a class.</p>
      {Object.entries(deadlineLabels).map(([key, label]) => <label className="row settings-row" key={key}><strong>{label}</strong>
        <input type="number" min={1} max={3600} value={deadlines[key] ?? ''} onChange={event => setDeadlines(current => ({ ...current, [key]: Number(event.target.value) }))} />
        <span>default {models.deadline_defaults?.[key]}</span></label>)}
      <button disabled={busy} onClick={() => void run(async () => { await api(token, 'settings/deadlines', { deadlines }); setMessage('Saved; the next call uses it.') }, 'Saving')}>Save</button></section>}
    {models?.configurable && <section><h2>Models</h2>
      {inClass && <p className="notice">A class is running; the model can be changed after it ends.</p>}
      {rows.map((row, index) => <div className="row settings-row" key={row.purpose}><strong>{row.purpose}</strong>
        <input disabled={inClass} value={row.adapter} placeholder="adapter" onChange={event => setRow(index, 'adapter', event.target.value)} />
        <input disabled={inClass} value={row.model} placeholder="model" onChange={event => setRow(index, 'model', event.target.value)} />
        <select disabled={inClass} value={row.effort} onChange={event => setRow(index, 'effort', event.target.value)}>{EFFORTS.map(effort => <option key={effort}>{effort}</option>)}</select>
        <select disabled={inClass} value={row.tier} onChange={event => setRow(index, 'tier', event.target.value)}>{TIERS.map(tier => <option key={tier}>{tier}</option>)}</select></div>)}
      <button disabled={busy || inClass} onClick={() => void run(async () => {
        const purposes = Object.fromEntries(rows.map(({ purpose, ...binding }) => [purpose, binding]))
        const before = bindingRows(models).find(row => row.purpose === 'classroom_judgment')
        await api(token, 'settings/bindings', { purposes })
        await api(token, 'settings/probe', {})
        setProbedClassroom(JSON.stringify(before) !== JSON.stringify(rows.find(row => row.purpose === 'classroom_judgment')))
        setMessage('Saved and probed.')
      }, 'Probing')}>Save and probe</button>
      <p>Changing a model needs a probe; until it passes, classes and AI work are refused.</p></section>}
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
