// A pretend class for the classroom sandbox: the page's /api/class/* calls and its event socket are
// answered here in the browser. Only enough of the core to move the screen; no judging, no records.
import type { AllowedAction, Snapshot } from '../src/client/types.gen'

// clicked: a task answered on the page (ADR-0020); view is what the core shows, right the answer.
type Task = { kind: 'question' | 'reading' | 'offscreen' | 'clicked'; text: string; reference?: string; view?: Snapshot['task']; right?: unknown; hint?: string }

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
  { kind: 'reading', text: 'Please read paragraph 3 aloud. When you finish, press "Finish and submit".', reference: 'She looked closely at the post. A young tree had grown in front of the sensor, and its leaves covered half of the small window.' },
  { kind: 'question', text: 'What did Mia change, and how did she check that it worked?' },
  { kind: 'offscreen', text: 'Away from the screen: find something at home that counts (a meter, a step counter...). Think how it could count wrong. Then come back and tell me.' },
  { kind: 'clicked', text: 'True or false: The app counted more people than Mia did.', hint: 'Look at the last two sentences of paragraph 2 and compare the numbers.',
    view: { type: 'choice', version: 1, variant: 'true_false', options: ['True', 'False'] }, right: { choice: 1 } },
  { kind: 'clicked', text: 'Why did the app count fewer people?', hint: 'Look at paragraph 3: what did Mia see at the post?',
    view: { type: 'choice', version: 1, variant: 'single', options: ['Fewer people hiked in the rain.', "Leaves covered half of the sensor's window.", 'The phone app was broken.'] }, right: { choice: 1 } },
  { kind: 'clicked', text: 'Put these in the order they happen in the story.', hint: 'First find the sentence where the numbers suddenly drop.',
    view: { type: 'order', version: 1, variant: 'events', items: ['Mia cut the branch back.', 'The numbers dropped by half.', 'Mia counted hikers for two hours.', 'The group checks the sensor once a month.'] }, right: { order: [1, 2, 0, 3] } },
  { kind: 'clicked', text: 'Match each English word with its meaning.', hint: 'Look at Words to watch beside the story.',
    view: { type: 'match', version: 1, left: ['volunteer', 'sensor', 'crossing'], right: ['a place to cross a river', 'to work for free', 'a device that notices and measures', 'a step counter'] }, right: { pairs: [1, 2, 0] } },
  { kind: 'clicked', text: 'Fill in the blank with a word from the story.', hint: 'Find the sentence with "She wrote down".',
    view: { type: 'fill', version: 1, variant: 'word_bank', text: 'She wrote down ___ people.', blanks: 1, bank: ['nineteen', 'forty-two', 'two'] }, right: { blanks: ['forty-two'] } },
  { kind: 'clicked', text: 'Fill in the blank with a word from the story.', hint: 'Look at the sentence with "The app showed only".',
    view: { type: 'fill', version: 1, variant: 'free', text: 'The app showed only ___ for the same two hours.', blanks: 1, bank: null }, right: { blanks: ['nineteen'] } },
  { kind: 'question', text: 'Tell the story in two or three sentences: what went wrong, and how did Mia find out?' },
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
  if (!task) return say(action('closing', 'That is all for today. You found why the sensor counted too few, and you read paragraph 3. Say you heard this summary, and we are done.', 'end_confirmation'),
    { task_index: tasks.length, wait_kind: 'closing_confirmation', recording: null })
  const after = task.kind === 'offscreen' ? 'offscreen' : 'input'
  return say(action('question', task.text, after, index === 0 ? { visuals: ['trail-sensor'] } : {}), {
    task_index: index, attempt_count: 0, wait_kind: 'answer', task_instance_id: `task-${index}`,
    recording: task.kind === 'reading' ? { reference_text: task.reference!, max_seconds: 60 } : null,
    task: task.kind === 'clicked' ? task.view : null,
  })
}

const base = (realm: Snapshot['realm']): Snapshot => ({
  activity_id: 'sandbox', learner_id: 'learner', realm, source_type: realm === 'study' ? 'student' : 'simulated',
  mode: 'waiting_input', activity_state: 'running', wait_kind: 'answer', task_instance_id: 'task', wait_id: 'wait',
  core_revision: 0, cancellation_epoch: 0, input_policy_ref: { object_id: 'policy', revision: 1 },
  pending_action: null, presented_action: null, attempt_count: 0, task_index: 0, task_count: tasks.length,
  elapsed_seconds: 0, end: null, actual_api: 'unverified', actual_voice: 'unverified', recording: null, task: null,
  materials: { 'passage': passage },
})

const opening = "I'm Jane. As you read, notice how Mia finds out that the app counts too few. Press Start reading when you are ready."

// The pretend vocabulary box: marked while reading, looked up after "I've finished reading".
const entry = (word: string, sentence: string, status: string, extra: Record<string, unknown> = {}) => ({
  surface: word.toLowerCase(), word, sentence, status, lemma: null, part_of_speech: null, definition: null, audio: false, ...extra,
})
const lookedUp = { sensor: { lemma: 'sensor', part_of_speech: 'noun', definition: 'a small thing that notices and measures something', audio: true },
  volunteers: { lemma: 'volunteer', part_of_speech: 'verb', definition: 'to do work without being paid', audio: true } } as Record<string, Record<string, unknown>>
const boxAfterReading = [
  entry('sensor', 'Last spring the group fixed a sensor to a wooden post near the river crossing.', 'ready', lookedUp.sensor),
  entry('crossing', 'Last spring the group fixed a sensor to a wooden post near the river crossing.', 'pending'),
  entry('agreed', 'This time her notebook and the app agreed within two people.', 'failed'),
]
async function wordMarked(body: { word: string, marked: boolean }) {
  const key = body.word.toLowerCase(), rest = (snapshot.vocabulary ?? []).filter(item => item.surface !== key)
  return set({ vocabulary: body.marked ? [...rest, entry(body.word, 'A sentence from the story.', 'marked')] : rest })
}
async function wordRegenerated(surface: string) {
  const pending = (snapshot.vocabulary ?? []).map(item => item.surface === surface ? { ...item, status: 'pending' } : item)
  set({ vocabulary: pending }); push()
  setTimeout(() => set({ vocabulary: (snapshot.vocabulary ?? []).map(item => item.surface === surface
    ? { ...item, status: 'ready', ...(lookedUp[surface] ?? { lemma: surface, part_of_speech: 'noun', definition: 'a simulated meaning of this word', audio: true }) } : item) }) && push(), 1200)
  return snapshot
}
// A fifth of a second of silence as a WAV, for the Listen buttons.
function silentWav() {
  const samples = 4800, data = new Uint8Array(44 + samples * 2), view = new DataView(data.buffer)
  const ascii = (at: number, text: string) => [...text].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)))
  ascii(0, 'RIFF'); view.setUint32(4, 36 + samples * 2, true); ascii(8, 'WAVEfmt '); view.setUint32(16, 16, true)
  view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, 24000, true); view.setUint32(28, 48000, true)
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); ascii(36, 'data'); view.setUint32(40, samples * 2, true)
  return new Response(data, { headers: { 'content-type': 'audio/wav' } })
}

export const scenes: Record<string, (realm: Snapshot['realm']) => void> = {
  'Opening (before reading)': realm => {
    snapshot = { ...base(realm), task_index: -1, materials: {} }
    say(action('goal', opening, 'input'), { wait_kind: 'reading_start' })
  },
  'Reading (timing)': realm => {
    snapshot = { ...base(realm), task_index: -1, wait_kind: 'reading' }
    snapshot.presented_action = action('goal', opening, 'input')
  },
  'Reading (marking words)': realm => {
    snapshot = { ...base(realm), task_index: -1, wait_kind: 'reading', vocabulary: [entry('sensor', 'Last spring the group fixed a sensor to a wooden post.', 'marked'), entry('crossing', 'Near the river crossing.', 'marked')] }
    snapshot.presented_action = action('goal', opening, 'input')
  },
  'Vocabulary box after reading': realm => { snapshot = { ...base(realm), vocabulary: boxAfterReading }; ask(0) },
  'Question (story and picture)': realm => { snapshot = base(realm); ask(0) },
  'Read aloud': realm => { snapshot = base(realm); ask(1) },
  'Jane reading the answer': realm => {
    snapshot = { ...base(realm), mode: 'judging' }
    snapshot.presented_action = action('question', tasks[0].text, 'input')
  },
  'Away from the screen': realm => {
    snapshot = { ...base(realm), task_index: 3, mode: 'offscreen', activity_state: 'offscreen', wait_kind: 'offscreen_return' }
    snapshot.presented_action = action('offscreen', tasks[3].text, 'offscreen')
  },
  'Paused': realm => {
    snapshot = { ...base(realm), mode: 'paused', activity_state: 'paused' }
    snapshot.presented_action = action('question', tasks[2].text, 'input')
  },
  'Wrap-up (waiting to confirm)': realm => { snapshot = base(realm); ask(tasks.length) },
  'Finished': realm => {
    snapshot = { ...base(realm), mode: 'ended', activity_state: 'ended', task_index: tasks.length, end: { reason: 'normal' } }
    snapshot.presented_action = action('closing', 'The class is over. Your evaluation comes next by itself.', 'end_confirmation')
  },
  'Very long story (scrolls)': realm => {
    snapshot = { ...base(realm), materials: { passage, notes, again: passage } }
    ask(2)
  },
  'No story': realm => { snapshot = { ...base(realm), materials: {} }; ask(2) },
  'True or false': realm => { snapshot = base(realm); ask(4) },
  'Single choice': realm => { snapshot = base(realm); ask(5) },
  'Order': realm => { snapshot = base(realm); ask(6) },
  'Match': realm => { snapshot = base(realm); ask(7) },
  'Fill (word bank)': realm => { snapshot = { ...base(realm), materials: { passage, notes } }; ask(8) },
  'Fill (free)': realm => { snapshot = base(realm); ask(9) },
  'Open question': realm => { snapshot = base(realm); ask(10) },
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
  if (intent === 'confirmation' && snapshot.wait_kind === 'closing_confirmation')
    return set({ mode: 'ended', activity_state: 'ended', end: { reason: 'normal' }, recording: null })
  if (intent === 'help') return say(action('help', 'Hint: look at the last two sentences of paragraph 2. How far apart are the app and her own count?', 'input'))
  if (snapshot.wait_kind === 'offscreen_report') return say(action('feedback', 'Thanks. You said clearly how it could count wrong. Let\'s go on.', 'next_task'))
  if (String(body.text).trim().length < 6)
    return say(action('feedback', 'Say a little more: how did she find out the app was wrong?', 'input'), { attempt_count: snapshot.attempt_count + 1 })
  return say(action('feedback', 'Yes. She counted for herself first, saw a big gap from the app, and then looked for the reason.', 'next_task'))
}

// A clicked task: right moves on with the prepared line, wrong says so and gives the hint.
const plain = (value: unknown) => JSON.stringify(value).toLowerCase().replace(/\s+/g, '')
async function responded(body: Body) {
  set({ mode: 'judging' }); push()
  await wait()
  const task = tasks[snapshot.task_index]
  if (body.intent === 'help') return say(action('help', task.hint ?? 'Read the story again.', 'input'))
  if (plain(body.response) === plain(task.right)) return say(action('feedback', 'Right! You found it.', 'next_task'), { attempt_count: snapshot.attempt_count + 1 })
  return say(action('help', `Not yet. ${task.hint ?? ''}`, 'retry'), { attempt_count: snapshot.attempt_count + 1 })
}

async function controlled(command: string) {
  switch (command) {
    // Like the core: a paused reading hides the story, and the story shows once reading starts.
    case 'pause': return set({ mode: 'paused', activity_state: 'paused', pending_action: null, ...(snapshot.wait_kind === 'reading' ? { materials: {} } : {}) })
    case 'resume': return set({ mode: 'waiting_input', activity_state: 'running', ...(snapshot.wait_kind === 'reading' ? { materials: { passage } } : {}) })
    case 'start_reading': return set({ wait_kind: 'reading', materials: { passage } })
    case 'finish_reading': {
      // Like the server: the first question comes at once; the meanings follow a little later.
      const marked = (snapshot.vocabulary ?? []).map(item => ({ ...item, status: 'pending' }))
      setTimeout(() => set({ vocabulary: (snapshot.vocabulary ?? []).map(item => ({ ...item, status: 'ready',
        ...(lookedUp[String(item.surface)] ?? { lemma: item.surface, part_of_speech: 'noun', definition: 'a simulated meaning of this word', audio: true }) })) }) && push(), 1500)
      set({ vocabulary: marked })
      return ask(0)
    }
    case 'stop': return set({ mode: 'ended', activity_state: 'ended', pending_action: null, recording: null, end: { reason: 'stop' } })
    case 'skip': return ask(snapshot.task_index + 1)
    case 'close': return ask(tasks.length)
    case 'repeat': return snapshot.presented_action ? say(action('repeat', snapshot.presented_action.text, 'input')) : snapshot
    case 'return': return say(action('question', 'Welcome back. What did you find, and how could it count wrong?', 'input'),
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
      : (set({ mode: 'judging' }), push(), await wait(), say(action('feedback', 'Clear reading. Say "leaves" and "window" a little more fully next time.', 'next_task'), { recording: null }))
    return json({ ack: 'accepted', snapshot: result, event_id: body.event_id })
  }
  if (route === 'class/response') return json({ ack: 'accepted', snapshot: await responded(body), event_id: body.event_id })
  if (route === 'class/control') return json(await controlled(body.command))
  if (route === 'class/word') return json(await wordMarked(body))
  if (route === 'class/word/regenerate') return json(await wordRegenerated(body.surface))
  if (route.startsWith('class/word-audio/')) return silentWav()
  if (route.startsWith('class/speech/')) {
    const id = decodeURIComponent(route.slice('class/speech/'.length))
    const said = [snapshot.pending_action, snapshot.presented_action].find(item => item?.action_id === id)
    return speech(said?.text ?? '', init?.signal ?? undefined)
  }
  if (route.startsWith('class/visual/')) return picture(decodeURIComponent(route.split('/').pop()!))
  if (route === 'class/playback') return json({})
  return new Response(JSON.stringify({ detail: `The sandbox has no ${route}` }), { status: 404 })
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
