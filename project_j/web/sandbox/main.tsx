// Classroom sandbox: the real Classroom component and stylesheet against a pretend class (mock.ts),
// so layout and interaction can be tried in any state without the backend, models or a course.
import { settings, load, scenes } from './mock'
import React, { useState } from 'react'
import ReactDOM from 'react-dom/client'
import Classroom from '../src/Classroom'
import '../src/style.css'

const names = Object.keys(scenes)
const saved = new URLSearchParams(location.hash.slice(1))

function Sandbox() {
  const [scene, setScene] = useState(names.includes(saved.get('scene') ?? '') ? saved.get('scene')! : names[0])
  const [study, setStudy] = useState(saved.get('study') === '1')
  const [run, setRun] = useState(0)
  const [waitSeconds, setWaitSeconds] = useState(settings.waitSeconds)
  settings.waitSeconds = waitSeconds
  const restart = (next: string, voiced: boolean) => {
    setScene(next); setStudy(voiced); setRun(value => value + 1)
    location.hash = new URLSearchParams({ scene: next, study: voiced ? '1' : '0' }).toString()
  }
  // Loaded before the classroom mounts, so its socket's first snapshot is this scene.
  const [loaded, setLoaded] = useState('')
  const key = `${scene}|${study}|${run}`
  if (loaded !== key) { load(scene, study ? 'study' : 'simulation'); setLoaded(key) }
  return <>
    <Classroom key={key} token="sandbox" onLeave={() => restart(names[0], study)} />
    <details className="sandbox-panel" open>
      <summary>Sandbox</summary>
      <label>Scene <select value={scene} onChange={event => restart(event.target.value, study)}>
        {names.map(name => <option key={name}>{name}</option>)}</select></label>
      <label><input type="checkbox" checked={study} onChange={event => restart(scene, event.target.checked)} /> Study mode (with sound)</label>
      <label>Wait <input type="number" min="0" step="0.5" value={waitSeconds} onChange={event => setWaitSeconds(Number(event.target.value))} /> s</label>
      <button className="quiet" onClick={() => restart(scene, study)}>Restart</button>
    </details>
  </>
}

const style = document.createElement('style')
style.textContent = `.sandbox-panel { position: fixed; left: 8px; bottom: 8px; z-index: 99; display: grid; gap: 6px; padding: 8px 10px;
  font-size: 12px; background: rgb(255 255 255 / .92); border: 1px solid #ccc; border-radius: 8px; box-shadow: 0 2px 8px rgb(0 0 0 / .12); }
.sandbox-panel summary { cursor: pointer; font-weight: 600; }
.sandbox-panel label { display: flex; align-items: center; gap: 6px; margin: 0; font-size: 12px; font-weight: normal; }
.sandbox-panel input, .sandbox-panel select { width: auto; min-height: 0; margin: 0; padding: 2px 4px; font-size: 12px; }
.sandbox-panel input[type=number] { width: 4em; }
.sandbox-panel button { padding: 2px 8px; font-size: 12px; }`
document.head.append(style)

ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><Sandbox /></React.StrictMode>)
