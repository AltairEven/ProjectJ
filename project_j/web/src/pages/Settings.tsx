import { useState } from 'react'
import { api, uid } from '../api'
import { modelLines } from '../report'
import { Rows, useRun, type PageProps } from '../ui'

export default function Settings({ token, dashboard, learner, refresh }: PageProps) {
  const { busy, run, setMessage, status } = useRun(refresh)
  const [backupId, setBackupId] = useState('')
  const [restored, setRestored] = useState<Record<string, unknown>>()
  const [lostDrafts, setLostDrafts] = useState<Record<string, string>>({})
  const mode = dashboard.realm === 'study' ? '正式学习' : '模拟'
  const pendingSeals = dashboard.pending_seals ?? []
  const sealErrors = Object.keys(dashboard.seal_errors ?? {})
  const exportRecords = () => { const requestId = uid(); void run(async () => {
    const data = await api<{ path: string }>(token, 'export', { request_id: requestId, learner_id: learner })
    const response = await fetch(`/api/export-file?path=${encodeURIComponent(data.path)}`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' })
    if (!response.ok) throw new Error('导出未取得')
    const url = URL.createObjectURL(await response.blob()); const anchor = document.createElement('a')
    anchor.href = url; anchor.download = `project-j-${dashboard.realm}.json`; anchor.click(); URL.revokeObjectURL(url)
  }, '导出') }
  return <>
    {status}
    <section><h2>模型与本机能力</h2>
      <p>数据空间：{mode}。{dashboard.verification?.result === 'pass' ? '已有与当前构建匹配的模拟检查依据。' : '模拟检查依据未取得。'}</p>
      {modelLines(dashboard.models as Record<string, any> | undefined).map(line => <p key={line}>{line}</p>)}</section>
    <section><h2>中断的课</h2>
      {pendingSeals.length > 0 && <p className="notice">有一堂课的结束事实尚未确认保存。请查询并接续原封存，不要重新开课补造。</p>}
      {(pendingSeals.length > 0 || sealErrors.length > 0) &&
        <button disabled={busy} onClick={() => void run(async () => { await api(token, 'retry-seal', {}) }, '接续封存')}>查询并接续原封存</button>}
      {dashboard.lost_activities?.map(item => { const id = String(item.activity_id); const reports = (item.reports ?? []) as { text: string, reported_at: string }[]
        return <div className="notice" key={id}><p>{new Date(String(item.requested_at)).toLocaleString()} 开始的课堂：{String(item.state)}</p>
          {reports.map(report => <p key={report.reported_at}>补记（{new Date(report.reported_at).toLocaleString()}）：{report.text}</p>)}
          <label>补记已知情况（只保存为新的来源，缺口保留，不进入评价）<textarea value={lostDrafts[id] ?? ''} onChange={event => setLostDrafts(drafts => ({ ...drafts, [id]: event.target.value }))} /></label>
          <button className="quiet" disabled={busy || !lostDrafts[id]?.trim()} onClick={() => { const requestId = uid(); void run(async () => {
            await api(token, 'lost-report', { request_id: requestId, learner_id: learner, activity_id: id, text: lostDrafts[id] })
            setLostDrafts(drafts => ({ ...drafts, [id]: '' })); setMessage('补记已保存；缺口保留，未调用评价。')
          }) }}>保存补记</button></div> })}
      {!pendingSeals.length && !sealErrors.length && !dashboard.lost_activities?.length && <p>没有中断的课。</p>}</section>
    <section><h2>备份与导出</h2><div className="row">
      <button className="quiet" disabled={busy} onClick={exportRecords}>导出{mode}记录</button>
      <button className="quiet" disabled={busy} onClick={() => void run(async () => { const data = await api<{ backup_id: string }>(token, 'backup', {}); setBackupId(data.backup_id); setMessage('数据库与固定素材已备份。') }, '备份')}>备份</button>
      <button className="quiet" disabled={busy || !backupId} onClick={() => { const newRun = `restored-${uid()}`; void run(async () => setRestored(await api(token, 'restore', { backup_id: backupId, new_run_id: newRun })), '恢复') }}>恢复到新目录</button></div>
      {restored && <Rows rows={Object.entries(restored)} />}</section>
  </>
}
