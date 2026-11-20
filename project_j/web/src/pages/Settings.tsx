import { useEffect, useState } from 'react'
import { api, saveFile, uid } from '../api'
import { dateTimeText } from '../dates'
import { modelLines, purposeLabels } from '../report'
import { ADAPTERS, EFFORTS, TIERS, bindingRows, checkConfirm, checkLine, deadlineLabels, modelOptions, probeNotes, withCurrent, type BindingRow } from '../settings'
import { JobLine, Rows, useRun, type PageProps } from '../ui'

export default function Settings({ token, dashboard, learner, refresh }: PageProps) {
  const { busy, run, setMessage, status } = useRun(refresh)
  const [backupId, setBackupId] = useState('')
  const [restored, setRestored] = useState<Record<string, unknown>>()
  const [lostDrafts, setLostDrafts] = useState<Record<string, string>>({})
  const models = dashboard.models as Record<string, any> | undefined
  const inClass = !!dashboard.active
  const [deadlines, setDeadlines] = useState<Record<string, number>>(() => ({ ...(models?.deadlines ?? {}) }))
  const [rows, setRows] = useState<BindingRow[]>(() => bindingRows(models))
  const [offered, setOffered] = useState<Record<string, string[]>>({})
  const configurable = !!models?.configurable
  const check = dashboard.judgment_check as Record<string, any> | null | undefined  // absent in class and in simulation
  const checking = (dashboard.jobs ?? []).find(job => job.name === 'reference')
  const [confirmCheck, setConfirmCheck] = useState(false)
  const runCheck = (mode: 'full' | 'rerun') => void run(async () => { await api(token, 'settings/reference', { mode }); setConfirmCheck(false) }, 'Starting the check')
  const seconds = (ms: number | null) => ms == null ? '—' : `${(ms / 1000).toFixed(1)} s`
  useEffect(() => {  // Not during a class; without a list the model is typed.
    if (configurable && !inClass) api<{ models: Record<string, string[]> }>(token, 'settings/models').then(data => setOffered(data.models), () => {})
  }, [token, configurable, inClass])
  const setRow = (index: number, field: keyof BindingRow, value: string) =>
    setRows(current => current.map((row, i) => i === index ? { ...row, [field]: value } : row))
  const mode = dashboard.realm === 'study' ? 'study' : 'simulation'
  const pendingSeals = dashboard.pending_seals ?? []
  const sealErrors = Object.keys(dashboard.seal_errors ?? {})
  const exportRecords = () => { const requestId = uid(); void run(async () => {
    const data = await api<{ path: string }>(token, 'export', { request_id: requestId, learner_id: learner })
    await saveFile(token, `/api/export-file?path=${encodeURIComponent(data.path)}`, `project-j-${dashboard.realm}.json`)
  }, 'Exporting') }
  return <>
    {status}
    <section><h2>Model and this computer</h2>
      <p>Data space: {mode}. {dashboard.verification?.result === 'pass' ? 'The checks match this build.' : 'The checks for this build are missing.'}</p>
      {modelLines(models).map(line => <p key={line}>{line}</p>)}
      {probeNotes(models).map(line => <p className="notice" key={line}>{line}</p>)}</section>
    {configurable && <section><h2>Waiting time</h2>
      <p>Seconds each kind of AI call may take; saved now, used by the next call, also during a class.</p>
      {Object.entries(deadlineLabels).map(([key, label]) => <label className="row settings-row" key={key}><strong>{label}</strong>
        <input type="number" min={1} max={3600} value={deadlines[key] ?? ''} onChange={event => setDeadlines(current => ({ ...current, [key]: Number(event.target.value) }))} />
        <span>default {models?.deadline_defaults?.[key]}</span></label>)}
      <button disabled={busy} onClick={() => void run(async () => { await api(token, 'settings/deadlines', { deadlines }); setMessage('Saved; the next call uses it.') }, 'Saving')}>Save</button></section>}
    {configurable && <section><h2>Models</h2>
      {inClass && <p className="notice">A class is running; the model can be changed after it ends.</p>}
      {rows.map((row, index) => { const options = modelOptions(offered, row)
        return <div className="row settings-row" key={row.purpose}><strong>{purposeLabels[row.purpose] ?? row.purpose}</strong>
        <select disabled={inClass} value={row.adapter} onChange={event => setRow(index, 'adapter', event.target.value)}>{withCurrent(ADAPTERS, row.adapter).map(adapter => <option key={adapter}>{adapter}</option>)}</select>
        {options
          ? <select disabled={inClass} value={row.model} onChange={event => setRow(index, 'model', event.target.value)}>{options.map(model => <option key={model}>{model}</option>)}</select>
          : <input disabled={inClass} value={row.model} placeholder="model" onChange={event => setRow(index, 'model', event.target.value)} />}
        <select disabled={inClass} value={row.effort} onChange={event => setRow(index, 'effort', event.target.value)}>{withCurrent(EFFORTS, row.effort).map(effort => <option key={effort}>{effort}</option>)}</select>
        <select disabled={inClass} value={row.tier} onChange={event => setRow(index, 'tier', event.target.value)}>{withCurrent(TIERS, row.tier).map(tier => <option key={tier}>{tier}</option>)}</select></div> })}
      <button disabled={busy || inClass} onClick={() => void run(async () => {
        const purposes = Object.fromEntries(rows.map(({ purpose, ...binding }) => [purpose, binding]))
        await api(token, 'settings/bindings', { purposes })
        setMessage('Saved: the new setup passed the probe and is in use.')
      }, 'Checking the new setup')}>Save and probe</button>
      <p>Saving probes the new setup first; it is used only if it passes, and otherwise the current setup stays.</p></section>}
    {configurable && check && <section><h2>Classroom judgment check</h2>
      <p>Current setup: {[check.binding.adapter, check.binding.model, check.binding.effort].join(' · ')}{check.binding.tier === 'fast' ? ' · Fast' : ''}</p>
      <p>{checkLine(check)}</p>
      {checking && <JobLine job={checking} />}
      {checking?.state !== 'running' && <>
        {confirmCheck
          ? <div className="notice"><p>{checkConfirm(check)}</p>
            <button disabled={busy} onClick={() => runCheck('full')}>Run it</button>
            <button className="quiet" disabled={busy} onClick={() => setConfirmCheck(false)}>Cancel</button></div>
          : <button className="quiet" disabled={busy} onClick={() => setConfirmCheck(true)}>{check.report ? `Run all ${check.cases} questions again` : 'Run the reference set'}</button>}
        {check.state === 'needs_rerun' && <button className="quiet" disabled={busy} onClick={() => runCheck('rerun')}>Rerun the {check.rerun_cases.length} failed questions</button>}</>}
      {check.report && <>
        <p>Last run {dateTimeText(check.report.captured_at)}: {check.report.passed}/{check.report.total} · median {seconds(check.report.median_ms)} · 95% {seconds(check.report.p95_ms)}</p>
        {check.state === 'needs_rerun' && <p>Calls that failed: {check.rerun_cases.join(', ')}</p>}
        {(check.report.failed as { case_id: string, reasons: string[], text: string | null }[]).map((item, index) =>
          <p key={index}>{item.case_id}: {item.reasons.join(', ')}{item.text ? ` (Jane: "${item.text}")` : ''}</p>)}</>}</section>}
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
