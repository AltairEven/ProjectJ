// Page preview: the real app (navigation, main page, courses, evaluations...) on a frozen dashboard
// taken from a simulation run (dashboard.json). Writes are accepted and change nothing.
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from '../src/App'
import '../src/style.css'
import dashboard from './dashboard.json'

const skills: [string, string, string][] = [
  ['verbs', '词类', '动词'], ['adjectives', '词类', '形容词'], ['adverbs', '词类', '副词'], ['nouns', '词类', '名词单复数'],
  ['prepositions', '词类', '介词'], ['articles', '词类', '冠词'], ['present_simple', '时态', '一般现在时'], ['past_simple', '时态', '一般过去时'],
  ['present_continuous', '时态', '现在进行时'], ['future', '时态', '将来时'], ['present_perfect', '时态', '现在完成时'],
  ['comparatives', '句型', '比较级与最高级'], ['questions', '句型', '疑问句'],
]
const course = dashboard.objects.find(doc => doc.kind === 'course')!
const nexts: Record<string, { kind: string, subject: Record<string, unknown> }> = {
  '评价等你认可（可重上）': dashboard.next as { kind: string, subject: Record<string, unknown> },
  '下一课想加强什么': { kind: 'focus', subject: { demand_ref: { object_id: 'demand', revision: 2 },
    options: skills.map(([skill_id, group, label]) => ({ skill_id, group, label })),
    suggestions: [{ skill_id: 'past_simple', reason: '上节课两处过去时用成了原形' }] } },
  '课程已确认，等你开始': { kind: 'course', subject: { course_ref: { object_id: course.object_id, revision: course.revision }, confirmed: true } },
  'AI 正在处理': { kind: 'waiting', subject: { failed: [] } },
  '建档：蓝思值': { kind: 'lexile', subject: {} },
  '建档：聊几句': { kind: 'chat', subject: { questions: ['What do you read in English at work, and what do you read for fun?', '你平时有什么爱好？', '每周大概能学几次、每次多久？', '你最想加强英语的哪一方面？'] } },
}
const jobs = { 'AI 正在处理': [{ name: 'prepare:demand', state: 'running', reason: null, progress: null }, { name: 'confirm:c:1', state: 'running', reason: null, progress: '3/9' }] } as Record<string, unknown[]>
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
  return <details className="sandbox-panel" open><summary>预览</summary>
    <label>下一步 <select value={value} onChange={event => {
      chosen = event.target.value; setValue(chosen)
      const params = new URLSearchParams(location.hash.slice(1)); params.set('next', chosen); location.hash = params.toString()
    }}>{Object.keys(nexts).map(name => <option key={name}>{name}</option>)}</select></label>
    <span>改完要等几秒（页面每 3 秒刷新）</span></details>
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
