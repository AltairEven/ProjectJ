// Page preview: the real app (navigation, main page, courses, evaluations...) on a frozen dashboard
// taken from a simulation run (dashboard.json). Writes are accepted and change nothing.
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from '../src/App'
import '../src/style.css'
import dashboard from './dashboard.json'

const skills: [string, string, string][] = [
  ['verbs', 'Word types', 'Verbs'], ['adjectives', 'Word types', 'Adjectives'], ['adverbs', 'Word types', 'Adverbs'], ['nouns', 'Word types', 'One and many'],
  ['prepositions', 'Word types', 'Prepositions'], ['articles', 'Word types', 'A, an, the'], ['present_simple', 'Tenses', 'Present simple'], ['past_simple', 'Tenses', 'Past simple'],
  ['present_continuous', 'Tenses', 'Present continuous'], ['future', 'Tenses', 'Future'], ['present_perfect', 'Tenses', 'Present perfect'],
  ['comparatives', 'Sentences', 'Comparing'], ['questions', 'Sentences', 'Questions'],
]
const course = dashboard.objects.find(doc => doc.kind === 'course')!
const nexts: Record<string, { kind: string, subject: Record<string, unknown> }> = {
  'Evaluation waiting (can retake)': dashboard.next as { kind: string, subject: Record<string, unknown> },
  'Next lesson topic and practice': { kind: 'focus', subject: { demand_ref: { object_id: 'demand', revision: 2 },
    options: skills.map(([skill_id, group, label]) => ({ skill_id, group, label })),
    suggestions: [{ skill_id: 'past_simple', reason: 'Two past verbs were in the base form last time' }],
    topics: [
      { title: 'Zelda: how Link glides over a canyon', reason: 'You said you like playing Zelda' },
      { title: 'Zelda: cooking in Hyrule', reason: 'The same game, another side' },
      { title: 'How an adventure game is made', reason: 'From the game to the people behind it' },
      { title: 'Maps and compasses in the real world', reason: 'Exploring, but in real life' },
    ], challenge: 'challenge', targets: { same: '500L', little: '550L', challenge: '600L', harder: '650L' }, } },
  'Jane listed no topics': { kind: 'focus', subject: { demand_ref: { object_id: 'demand', revision: 2 },
    options: skills.map(([skill_id, group, label]) => ({ skill_id, group, label })), suggestions: [], topics: [],
    challenge: 'challenge', targets: { same: '500L', little: '550L', challenge: '600L', harder: '650L' }, } },
  'Lesson confirmed, waiting to start': { kind: 'course', subject: { course_ref: { object_id: course.object_id, revision: course.revision }, confirmed: true } },
  'Jane is working': { kind: 'waiting', subject: { failed: [] } },
  'Getting started: Lexile': { kind: 'lexile', subject: {} },
  'Getting started: chat': { kind: 'chat', subject: { questions: ['Why do you learn English?', 'What do you like to do?', 'How much time can you study each week?', 'What do you want to do better: reading, speaking, listening or saying words?'] } },
}
const jobs = { 'Jane is working': [{ name: 'prepare:demand', state: 'running', reason: null, progress: null }, { name: 'confirm:c:1', state: 'running', reason: null, progress: '3/9' }] } as Record<string, unknown[]>
let chosen = new URLSearchParams(location.hash.slice(1)).get('next') ?? Object.keys(nexts)[0]

const realFetch = window.fetch.bind(window)
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
  if (!path.startsWith('/api/')) return realFetch(input, init)
  const body = path === '/api/dashboard' ? { ...dashboard, next: nexts[chosen] ?? nexts[Object.keys(nexts)[0]], jobs: jobs[chosen] ?? [] } : {}
  await new Promise(done => setTimeout(done, init?.method === 'POST' ? 600 : 50))
  return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
}

function Panel() {
  const [value, setValue] = React.useState(chosen)
  return <details className="sandbox-panel" open><summary>Preview</summary>
    <label>Next step <select value={value} onChange={event => {
      chosen = event.target.value; setValue(chosen)
      const params = new URLSearchParams(location.hash.slice(1)); params.set('next', chosen); location.hash = params.toString()
    }}>{Object.keys(nexts).map(name => <option key={name}>{name}</option>)}</select></label>
    <span>Changes show within a few seconds (the page refreshes every 3 s)</span></details>
}

if (!new URLSearchParams(location.hash.slice(1)).get('token')) location.hash = 'token=sandbox&page=home'
const style = document.createElement('style')
style.textContent = `.sandbox-panel { position: fixed; left: 8px; bottom: 8px; z-index: 99; display: grid; gap: 6px; padding: 8px 10px; font-size: 12px;
  background: rgb(255 255 255 / .94); border: 1px solid #ccc; border-radius: 8px; box-shadow: 0 2px 8px rgb(0 0 0 / .12); }
.sandbox-panel summary { cursor: pointer; font-weight: 600; }
.sandbox-panel label { display: flex; align-items: center; gap: 6px; margin: 0; font-size: 12px; font-weight: normal; }
.sandbox-panel select { font-size: 12px; }`
document.head.append(style)
ReactDOM.createRoot(document.getElementById('root')!).render(<><React.StrictMode><App /></React.StrictMode><Panel /></>)
