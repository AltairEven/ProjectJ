import { useState } from 'react'
import { fileUrl } from './api'
import { statusLabel, type Entry } from './vocabulary'

/** Fetch one word's WAV with the token and play it once; the object URL is released afterwards. */
export async function playWord(token: string, path: string): Promise<void> {
  const url = await fileUrl(token, path)
  const audio = new Audio(url)
  audio.addEventListener('ended', () => URL.revokeObjectURL(url), { once: true })
  await audio.play()
}

type Props = {
  token: string, entries: Entry[], audioUrl: (lemma: string) => string,
  onRegenerate?: (key: string) => void, title?: string, busy?: boolean,
}

// The words the student marked, folded away until wanted: word, part of speech, meaning, sound.
// Entries are shown as the server sent them; nothing here is scored.
export function VocabularyBox({ token, entries, audioUrl, onRegenerate, title = 'Vocabulary box', busy }: Props) {
  const [message, setMessage] = useState('')
  if (!entries.length) return null
  const play = (lemma: string) => void playWord(token, audioUrl(lemma)).catch(() => setMessage('The sound did not come back. Try again.'))
  return <details className="vocabulary-box">
    <summary>{title} ({entries.length})</summary>
    {message && <p className="notice" role="alert">{message}</p>}
    <ul>{entries.map(entry => {
      const key = entry.surface ?? entry.lemma ?? ''
      const label = statusLabel(entry)
      return <li key={key}>
        <strong>{entry.word ?? entry.lemma}</strong>
        {entry.part_of_speech && <em> {entry.part_of_speech}</em>}
        {label === 'Ready' ? <span> — {entry.definition}</span> : <span className="tag">{label}</span>}
        <span className="row">
          {label === 'Ready' && entry.audio && entry.lemma && <button className="quiet" type="button" disabled={busy} onClick={() => play(entry.lemma!)}>Listen</button>}
          {onRegenerate && label !== 'Getting ready…' && <button className="quiet" type="button" disabled={busy} onClick={() => onRegenerate(key)}>Regenerate</button>}
        </span>
      </li>
    })}</ul>
  </details>
}
