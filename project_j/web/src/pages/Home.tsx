import { api } from '../api'
import type { Document as Doc } from '../client/types.gen'
import { jobLines, nextStepView } from '../next-step'
import { endLabels } from '../report'
import { Rows, useRun, type PageProps } from '../ui'

const newest = (docs: Doc[]) => [...docs].sort((a, b) => b.created_at.localeCompare(a.created_at))[0] as Doc | undefined

export default function Home({ token, dashboard, learner, go, refresh }: PageProps) {
  const { busy, run, status } = useRun(refresh)
  const view = nextStepView(dashboard.next ?? { kind: 'none' })
  const objects = dashboard.objects ?? []
  const jobs = dashboard.jobs ?? []
  const fact = newest(objects.filter(doc => doc.kind === 'fact'))
  const activity = fact && String(fact.body.activity_id)
  const course = fact && objects.find(doc => doc.kind === 'course' && doc.object_id === (fact.body.course_ref as Doc).object_id)
  const evaluation = activity === undefined ? undefined : objects.find(doc => doc.kind === 'evaluation' && doc.body.activity_id === activity)
  const week = objects.find(doc => doc.kind === 'plan' && doc.body.level === 'week')
  const retry = (name: string) => run(async () => { await api(token, 'jobs/retry', { learner_id: learner, name }) })
  return <>
    <section className="next"><h2>下一步</h2><p className="next-title">{view.title}</p>
      {view.action && <button onClick={() => go(view.page, view.detail)}>{view.action}</button>}</section>
    {status}
    <section><h2>后台进行中</h2>
      {jobs.length ? <ul>{jobs.map(job => <li key={job.name}>{jobLines([job])[0]}
        {/* A failed start is retried from its course, where the materials are confirmed again. */}
        {job.state === 'failed' && !job.name.startsWith('start:') &&
          <> <button className="quiet" disabled={busy} onClick={() => void retry(job.name)}>重试</button></>}</li>)}</ul>
        : <p>没有正在进行的事。</p>}</section>
    <section><h2>最近一课</h2>{fact ? <>
      <Rows rows={[
        ['课程', (course?.body.parent_preview as Record<string, string> | undefined)?.story],
        ['结束', endLabels[String((fact.body.end as Record<string, unknown>)?.reason)] ?? '未知'],
        ['评价', (evaluation?.body.report as Record<string, string> | undefined)?.summary ?? '评价中或尚未生成'],
      ]} />
      <button className="quiet" onClick={() => go('evaluations', activity)}>查看评价</button></> : <p>还没有上过课。</p>}</section>
    <section><h2>本周计划</h2>{week ? <Rows rows={[
      ['主要目标', (week.body.main_goal as Record<string, string>).description],
      ['下次复盘', week.body.review_at ? new Date(String(week.body.review_at)).toLocaleDateString() : '未定'],
    ]} /> : <p>还没有学习计划。</p>}</section>
  </>
}
