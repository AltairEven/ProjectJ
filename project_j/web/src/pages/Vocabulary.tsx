import { api, uid } from '../api'
import { Rows, useRun, type PageProps } from '../ui'
import { playWord } from '../VocabularyBox'
import { entryRows, statusLabel, type Entry } from '../vocabulary'

// Every word the student ever marked, with everything the record knows about it (design/learning).
// Reading, listening and asking for a new meaning only; words leave by the rule, never by hand.
export default function Vocabulary({ token, dashboard, learner, refresh }: PageProps) {
  const rows = (dashboard.vocabulary ?? []) as unknown as Entry[]
  const active = rows.filter(row => row.status !== 'mastered'), mastered = rows.filter(row => row.status === 'mastered')
  const { busy, run, status } = useRun(refresh)
  const regenerate = (lemma: string) => void run(async () => {
    await api(token, 'vocabulary/regenerate', { request_id: uid(), learner_id: learner, lemma })
  }, `Looking up "${lemma}"`)
  const listen = (lemma: string) => void run(() => playWord(token, `/api/vocabulary/audio?lemma=${encodeURIComponent(lemma)}&learner_id=${encodeURIComponent(learner)}`))
  const group = (title: string, items: Entry[], open: boolean) => <details open={open}>
    <summary>{title} ({items.length})</summary>
    {items.length ? <ul className="list vocabulary">{items.map(row => <li key={row.lemma}>
      <details><summary>{row.lemma} <span className="tag">{statusLabel(row)}</span></summary>
        <Rows rows={entryRows(row)} />
        <div className="row">
          {row.audio && <button className="quiet" disabled={busy} onClick={() => listen(row.lemma!)}>Listen</button>}
          <button className="quiet" disabled={busy} onClick={() => regenerate(row.lemma!)}>Regenerate</button>
        </div></details></li>)}</ul> : <p>None yet.</p>}
  </details>
  return <section><h2>Vocabulary</h2>
    <p>{active.length} word{active.length === 1 ? '' : 's'} to learn · {mastered.length} mastered</p>
    <p className="hint">Tap a word while you read a story to mark it. A word you meet five more times without marking it counts as mastered.</p>
    {status}
    {group('Words to learn', active, true)}
    {group('Mastered', mastered, false)}
  </section>
}
