// A pretend class for the classroom sandbox: the page's /api/class/* calls and its event socket are
// answered here in the browser. Only enough of the core to move the screen; no judging, no records.
import type { AllowedAction, Snapshot } from '../src/client/types.gen'

type Task = { kind: 'question' | 'reading' | 'offscreen'; text: string; reference?: string }

const passage = `Checking a Trail Sensor

Mia volunteers with a small group that looks after the hiking trails above her town. Last spring the group fixed a sensor to a wooden post near the river crossing. The sensor counts how many people walk past each day and sends the number to a phone app.

In June the numbers suddenly dropped by half. Some members thought fewer people were hiking because of the rain. Mia was not so sure. She walked up to the post on a dry Saturday morning and counted the hikers herself for two hours. She wrote down forty-two people. The app showed only nineteen for the same two hours.

She looked closely at the post. A young tree had grown in front of the sensor, and its leaves covered half of the small window. Mia cut the branch back, cleaned the window and counted again the next weekend. This time her notebook and the app agreed within two people.

The group now checks the sensor once a month. Mia says the lesson is simple: before you trust a number, find out how it was measured.`

const notes = `Words to watch

volunteer — to do work without being paid
sensor — a small device that notices and measures something
crossing — a place where a path goes over a river or road
agree within two — differ by no more than two`

const tasks: Task[] = [
  { kind: 'question', text: 'Why did Mia go up to the post herself instead of trusting the app? Answer in one or two sentences.' },
  { kind: 'reading', text: '请朗读第三段。读完按“结束并提交”。', reference: 'She looked closely at the post. A young tree had grown in front of the sensor, and its leaves covered half of the small window.' },
  { kind: 'question', text: 'What did Mia change, and how did she check that it worked?' },
  { kind: 'offscreen', text: '离开屏幕：找一样你家里会“计数”的东西（电表、计步器……），想想它可能怎样数错。回来后说说你发现了什么。' },
]

let snapshot: Snapshot
let revision = 0
let actionCount = 0
let socket: { onmessage?: ((event: { data: string }) => void) | null } | undefined
export const settings = { waitSeconds: 1.5 }

const sleep = (ms: number, signal?: AbortSignal) => new Promise<void>((done, fail) => {
  const timer = setTimeout(done, ms)
  signal?.addEventListener('abort', () => { clearTimeout(timer); fail(new DOMException('aborted', 'AbortError')) })
})
const wait = (signal?: AbortSignal) => sleep(settings.waitSeconds * 1000, signal)

function action(kind: AllowedAction['kind'], text: string, after: AllowedAction['after_delivery'], extra: Partial<AllowedAction> = {}): AllowedAction {
  return { action_id: `action-${++actionCount}`, activity_id: 'sandbox', task_instance_id: `task-${snapshot?.task_index ?? -1}`,
    wait_id: `wait-${actionCount}`, core_revision: revision + 1, cancellation_epoch: 0, kind, text, after_delivery: after, ...extra }
}

function set(patch: Partial<Snapshot>) {
  snapshot = { ...snapshot, ...patch, core_revision: ++revision }
  return snapshot
}
const push = () => socket?.onmessage?.({ data: JSON.stringify({ snapshot }) })
const say = (pending: AllowedAction, patch: Partial<Snapshot> = {}) =>
  set({ mode: 'delivering', activity_state: 'running', ...patch, pending_action: pending })

// Jane asks task n: the reading text appears with a reading task, pictures with the first question.
function ask(index: number) {
  const task = tasks[index]
  if (!task) return say(action('closing', '今天就到这里。你找到了传感器为什么少数，也读完了第三段。确认收到这段小结就下课。', 'end_confirmation'),
    { task_index: tasks.length, wait_kind: 'closing_confirmation', recording: null })
  const after = task.kind === 'offscreen' ? 'offscreen' : 'input'
  return say(action('question', task.text, after, index === 0 ? { visuals: ['trail-sensor'] } : {}), {
    task_index: index, attempt_count: 0, wait_kind: 'answer',
    recording: task.kind === 'reading' ? { reference_text: task.reference!, max_seconds: 60 } : null,
  })
}

const base = (realm: Snapshot['realm']): Snapshot => ({
  activity_id: 'sandbox', learner_id: 'learner', realm, source_type: realm === 'study' ? 'student' : 'simulated',
  mode: 'waiting_input', activity_state: 'running', wait_kind: 'answer', task_instance_id: 'task', wait_id: 'wait',
  core_revision: 0, cancellation_epoch: 0, input_policy_ref: { object_id: 'policy', revision: 1 },
  pending_action: null, presented_action: null, attempt_count: 0, task_index: 0, task_count: tasks.length,
  elapsed_seconds: 0, end: null, actual_api: '未验证', actual_voice: '未验证', recording: null,
  materials: { 'passage': passage },
})

export const scenes: Record<string, (realm: Snapshot['realm']) => void> = {
  '开场说明（等确认）': realm => {
    snapshot = { ...base(realm), task_index: -1, materials: {} }
    say(action('goal', '今天我们读一篇关于登山步道传感器的短文，练习从文中找原因、说清楚经过，最后朗读一段。准备好就按“确认收到说明”。', 'input'),
      { wait_kind: 'goal_confirmation' })
  },
  '问答题（有课文和图）': realm => { snapshot = base(realm); ask(0) },
  '朗读段': realm => { snapshot = base(realm); ask(1) },
  'Jane 在看回答': realm => {
    snapshot = { ...base(realm), mode: 'judging' }
    snapshot.presented_action = action('question', tasks[0].text, 'input')
  },
  '离屏活动中': realm => {
    snapshot = { ...base(realm), task_index: 3, mode: 'offscreen', activity_state: 'offscreen', wait_kind: 'offscreen_return' }
    snapshot.presented_action = action('offscreen', tasks[3].text, 'offscreen')
  },
  '已暂停': realm => {
    snapshot = { ...base(realm), mode: 'paused', activity_state: 'paused' }
    snapshot.presented_action = action('question', tasks[2].text, 'input')
  },
  '收尾（等确认）': realm => { snapshot = base(realm); ask(tasks.length) },
  '已结束': realm => {
    snapshot = { ...base(realm), mode: 'ended', activity_state: 'ended', task_index: tasks.length, end: { reason: 'normal' } }
    snapshot.presented_action = action('closing', '这节课结束了，课后评价会自动生成。', 'end_confirmation')
  },
  '很长的课文（滚动）': realm => {
    snapshot = { ...base(realm), materials: { passage, notes, again: passage } }
    ask(2)
  },
  '没有课文': realm => { snapshot = { ...base(realm), materials: {} }; ask(2) },
}

export function load(scene: string, realm: Snapshot['realm']) {
  revision = 0
  scenes[scene](realm)
}

type Body = Record<string, any>

// The delivered sentence becomes the shown one; a few actions move the class on by themselves.
function delivered(report: Body) {
  const pending = snapshot.pending_action
  if (!pending || report.action_id !== pending.action_id || report.status !== 'completed' || report.modality === 'image') return snapshot
  const shown = { ...snapshot, presented_action: pending, pending_action: null }
  if (pending.after_delivery === 'next_task') { snapshot = shown; return ask(snapshot.task_index + 1) }
  if (pending.after_delivery === 'offscreen') return set({ ...shown, mode: 'offscreen', activity_state: 'offscreen', wait_kind: 'offscreen_return' })
  return set({ ...shown, mode: 'waiting_input' })
}

async function answered(body: Body) {
  set({ mode: 'judging' }); push()
  await wait()
  const intent = body.intent as string
  if (intent === 'confirmation' && snapshot.wait_kind === 'goal_confirmation') { set({ materials: { passage } }); return ask(0) }
  if (intent === 'confirmation' && snapshot.wait_kind === 'closing_confirmation')
    return set({ mode: 'ended', activity_state: 'ended', end: { reason: 'normal' }, recording: null })
  if (intent === 'help') return say(action('help', '提示：看第二段最后两句，app 和她自己数的数字差了多少？', 'input'))
  if (snapshot.wait_kind === 'offscreen_report') return say(action('feedback', '谢谢，你说清楚了它可能怎样数错。我们来收个尾。', 'next_task'))
  if (String(body.text).trim().length < 6)
    return say(action('feedback', '再多说一点：她是怎样发现 app 的数字不对的？', 'input'), { attempt_count: snapshot.attempt_count + 1 })
  return say(action('feedback', '对，她先自己数了一遍，发现和 app 差了一大截，才去找原因。', 'next_task'))
}

async function controlled(command: string) {
  switch (command) {
    case 'pause': return set({ mode: 'paused', activity_state: 'paused', pending_action: null })
    case 'resume': return set({ mode: 'waiting_input', activity_state: 'running' })
    case 'stop': return set({ mode: 'ended', activity_state: 'ended', pending_action: null, recording: null, end: { reason: 'stop' } })
    case 'skip': return ask(snapshot.task_index + 1)
    case 'close': return ask(tasks.length)
    case 'repeat': return snapshot.presented_action ? say(action('repeat', snapshot.presented_action.text, 'input')) : snapshot
    case 'return': return say(action('question', '欢迎回来。说说你找到了什么、它可能怎样数错？', 'input'),
      { mode: 'delivering', activity_state: 'running', wait_kind: 'offscreen_report' })
  }
  return snapshot
}

// Silent PCM16 at 24 kHz in half-second pieces, after the usual preparing wait.
function speech(text: string, signal?: AbortSignal) {
  const seconds = Math.min(1.5 + text.length * 0.03, 5), piece = new Uint8Array(24000)
  let left = Math.ceil(seconds * 2), first = true
  return new Response(new ReadableStream({
    async pull(controller) {
      try { await (first ? wait(signal) : sleep(100, signal)) } catch { controller.error(new DOMException('aborted', 'AbortError')); return }
      first = false
      if (left-- > 0) controller.enqueue(piece); else controller.close()
    },
  }), { headers: { 'content-type': 'audio/pcm;rate=24000' } })
}

const picture = (name: string) => new Response(`<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" viewBox="0 0 640 400">
  <rect width="640" height="400" fill="#dfe9e1"/><rect x="290" y="120" width="40" height="230" fill="#8a6a4a"/>
  <rect x="270" y="150" width="80" height="50" rx="6" fill="#3d4b55"/><circle cx="310" cy="175" r="12" fill="#9fd3f0"/>
  <circle cx="380" cy="150" r="70" fill="#6f9e5c" opacity=".85"/><text x="20" y="380" font-family="sans-serif" font-size="20" fill="#3d4b55">${name}</text></svg>`,
{ headers: { 'content-type': 'image/svg+xml' } })

const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } })

const realFetch = window.fetch.bind(window)
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
  if (!path.startsWith('/api/')) return realFetch(input, init)
  const body = init?.body ? JSON.parse(String(init.body)) : {}
  const route = path.slice('/api/'.length)
  if (route === 'class/delivery') return json(delivered(body))
  if (route === 'class/input' || route === 'class/recording') {
    const result = route === 'class/input' ? await answered(body)
      : (set({ mode: 'judging' }), push(), await wait(), say(action('feedback', '读得很清楚。“leaves”和“window”再读得饱满一点就更好了。', 'next_task'), { recording: null }))
    return json({ ack: 'accepted', snapshot: result, event_id: body.event_id })
  }
  if (route === 'class/control') return json(await controlled(body.command))
  if (route.startsWith('class/speech/')) {
    const id = decodeURIComponent(route.slice('class/speech/'.length))
    const said = [snapshot.pending_action, snapshot.presented_action].find(item => item?.action_id === id)
    return speech(said?.text ?? '', init?.signal ?? undefined)
  }
  if (route.startsWith('class/visual/')) return picture(decodeURIComponent(route.split('/').pop()!))
  if (route === 'class/playback') return json({})
  return new Response(JSON.stringify({ detail: `沙盒没有 ${route}` }), { status: 404 })
}

// Only the class event stream is pretend; Vite's own reload socket stays real.
const RealSocket = window.WebSocket
window.WebSocket = function (url: string | URL, protocols?: string | string[]) {
  if (!String(url).includes('/api/class/events')) return new RealSocket(url, protocols)
  const fake = { onmessage: null as ((event: { data: string }) => void) | null, onerror: null, close() { if (socket === fake) socket = undefined } }
  socket = fake
  setTimeout(() => fake.onmessage?.({ data: JSON.stringify({ snapshot }) }), 30)
  return fake
} as unknown as typeof WebSocket
