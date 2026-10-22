import { dimensionLines } from '../learning'
import { recentLine, scoreRows, type LessonScore } from '../scores'
import type { PageProps } from '../ui'
import { ProfileForm } from './Onboarding'

export default function State({ token, dashboard, learner, refresh }: PageProps) {
  const objects = dashboard.objects ?? []
  const profile = objects.find(doc => doc.kind === 'profile')
  const state = objects.find(doc => doc.kind === 'state')
  const catalog = objects.filter(doc => doc.kind === 'capability')
  const scores = (dashboard.scores ?? []) as unknown as LessonScore[]
  return <>
    <section><h2>How you are doing</h2>{state ? <dl className="rows">{dimensionLines(state, catalog).map(line =>
      <div key={line.name}><dt>{line.name}</dt><dd><strong>{line.judgment}</strong> {line.basis}</dd></div>)}</dl> : <p>No progress yet.</p>}</section>
    <section><h2>Scores</h2>{scores.length ? <>
      <p>{recentLine(scores, catalog)} (out of 10; unknown scores are left out)</p>
      <dl className="rows">{scoreRows(scores, objects).map(row =>
        <div key={row.key}><dt>{row.date} · {row.title}</dt><dd>{row.types}<br />{row.dimensions}</dd></div>)}</dl>
    </> : <p>No scores yet. They come after a lesson and its evaluation.</p>}</section>
    {profile && <ProfileForm key={`${profile.object_id}:${profile.revision}`} token={token} learner={learner} refresh={refresh} profile={profile} objects={objects}
      title="Your profile" intro="Lessons are chosen and made from this. Changes count from the next plan and lesson; lessons already made stay as they are." action="Save changes" />}
    <section><h2>Skills</h2>{catalog.length ? <dl className="rows">{catalog.map(doc =>
      <div key={doc.object_id}><dt>{String(doc.body.name)}</dt><dd>{String(doc.body.definition)}</dd></div>)}</dl> : <p>No skills yet.</p>}</section>
  </>
}
