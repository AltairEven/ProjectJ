import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import Classroom from './Classroom'
import type { Dashboard } from './client/types.gen'
import { inOnboarding, pages, type Page, type Target } from './next-step'
import Courses from './pages/Courses'
import Evaluations from './pages/Evaluations'
import Home from './pages/Home'
import Onboarding from './pages/Onboarding'
import Plan from './pages/Plan'
import Settings from './pages/Settings'
import State from './pages/State'
import Vocabulary from './pages/Vocabulary'
import type { PageProps } from './ui'

const views: Record<Page | 'onboarding', (props: PageProps) => React.ReactNode> = {
  home: Home, state: State, plan: Plan, courses: Courses, evaluations: Evaluations, vocabulary: Vocabulary, settings: Settings, onboarding: Onboarding,
}

function Shell({ token, page, detail }: { token: string, page: Page, detail?: string }) {
  const [dashboard, setDashboard] = useState<Dashboard>()
  const [error, setError] = useState('')
  const [classroom, setClassroom] = useState(false)
  const left = useRef(new Set<string>())  // Ended classes the student left; not entered again.
  const refresh = useCallback(async () => { setDashboard(await api<Dashboard>(token, 'dashboard')); setError('') }, [token])
  const active = dashboard?.active?.activity_id
  if (active && !classroom && !left.current.has(active)) setClassroom(true)
  // The classroom has its own event stream; the main page polls only outside class.
  useEffect(() => {
    if (classroom) return
    const tick = () => void refresh().catch(failure => setError((failure as Error).message))
    tick()
    const timer = setInterval(tick, 3000)
    return () => clearInterval(timer)
  }, [refresh, classroom])
  const go = (target: Target, next?: string) => {
    if (target === 'class') { setClassroom(true); return }
    const params = new URLSearchParams({ token, page: target })
    if (next) params.set('detail', next)
    location.hash = params.toString()
  }
  if (classroom) return <Classroom token={token} onLeave={() => { if (active) left.current.add(active); setClassroom(false); go('home') }} />
  if (!dashboard) return <main><p>{error || 'Loading…'}</p></main>
  const onboarding = inOnboarding(dashboard)
  const shown: Page | 'onboarding' = onboarding ? 'onboarding' : page
  const View = views[shown]
  return <div className="shell">
    <nav aria-label="Sections"><span className="brand">Project J<span className="tag">{dashboard.realm === 'study' ? 'Study' : 'Simulation'}</span></span>
      {pages.map(([key, label]) => <button key={key} className={key === shown ? '' : 'quiet'} disabled={onboarding}
        aria-current={key === shown ? 'page' : undefined} onClick={() => go(key)}>{label}</button>)}</nav>
    <main>{error && <div className="notice" role="alert">{error}</div>}
      <View token={token} dashboard={dashboard} learner={dashboard.learner_id ?? ''} detail={detail} go={go} refresh={refresh} /></main>
  </div>
}

export default function App() {
  const [hash, setHash] = useState(location.hash)
  useEffect(() => {
    const listen = () => setHash(location.hash)
    addEventListener('hashchange', listen)
    return () => removeEventListener('hashchange', listen)
  }, [])
  const params = new URLSearchParams(hash.slice(1))
  const token = params.get('token')
  const page = pages.some(([key]) => key === params.get('page')) ? params.get('page') as Page : 'home'
  return token ? <Shell token={token} page={page} detail={params.get('detail') ?? undefined} />
    : <main><h1>Project J</h1><p>Please open the link the start command printed.</p></main>
}
