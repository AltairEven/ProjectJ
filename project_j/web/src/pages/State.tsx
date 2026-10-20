import { dimensionLines } from '../learning'
import type { PageProps } from '../ui'
import { ProfileForm } from './Onboarding'

export default function State({ token, dashboard, learner, refresh }: PageProps) {
  const objects = dashboard.objects ?? []
  const profile = objects.find(doc => doc.kind === 'profile')
  const state = objects.find(doc => doc.kind === 'state')
  const catalog = objects.filter(doc => doc.kind === 'capability')
  return <>
    <section><h2>各方面情况</h2>{state ? <dl className="rows">{dimensionLines(state, catalog).map(line =>
      <div key={line.name}><dt>{line.name}</dt><dd><strong>{line.judgment}</strong>　{line.basis}</dd></div>)}</dl> : <p>还没有学习状态。</p>}</section>
    {profile && <ProfileForm key={`${profile.object_id}:${profile.revision}`} token={token} learner={learner} refresh={refresh} profile={profile} objects={objects}
      title="学习资料" intro="选课与备课都参考这份资料；修改从下一次规划和备课起生效，已备好的课不受影响。" action="保存修改" />}
    <section><h2>能力条目</h2>{catalog.length ? <dl className="rows">{catalog.map(doc =>
      <div key={doc.object_id}><dt>{String(doc.body.name)}</dt><dd>{String(doc.body.definition)}</dd></div>)}</dl> : <p>还没有能力条目。</p>}</section>
  </>
}
