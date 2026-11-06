import { useEffect, useMemo, useState } from 'react'
import {
  Activity, Bell, Bot, FileText, History, LayoutDashboard, LogOut, Map as MapIcon, Network, Plug, ScrollText, Settings as SettingsIcon, Terminal, Timer, X, Radio, Crosshair,
} from 'lucide-react'
import { api, ApiError, getToken } from './api'
import { useStore } from './store'
import { Btn, clsx, inputCls } from './lib'
import type { ViewId } from './types'
import Inspector from './components/Inspector'
import TimeControls from './components/TimeControls'
import Overview from './views/Overview'
import LiveMap from './views/LiveMap'
import Agents from './views/Agents'
import NetworkView from './views/Network'
import Filesystem from './views/Filesystem'
import Processes from './views/Processes'
import Commands from './views/Commands'
import Timeline from './views/Timeline'
import HistoryView from './views/History'
import Alerts from './views/Alerts'
import Integrations from './views/Integrations'
import Settings from './views/Settings'

const NAV: { id: ViewId; label: string; icon: any }[] = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'map', label: 'Live Activity Map', icon: MapIcon },
  { id: 'agents', label: 'Agents', icon: Bot },
  { id: 'network', label: 'Network', icon: Network },
  { id: 'filesystem', label: 'Filesystem', icon: FileText },
  { id: 'processes', label: 'Processes', icon: Terminal },
  { id: 'commands', label: 'Commands', icon: ScrollText },
  { id: 'timeline', label: 'Timeline', icon: Timer },
  { id: 'history', label: 'Event History', icon: History },
  { id: 'alerts', label: 'Alerts', icon: Bell },
  { id: 'integrations', label: 'Integrations', icon: Plug },
  { id: 'settings', label: 'Settings', icon: SettingsIcon },
]

function Login() {
  const login = useStore((s) => s.login)
  const [u, setU] = useState('admin')
  const [p, setP] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr('')
    try {
      const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: u, password: p }) })
      const j = await r.json()
      if (!r.ok) throw new Error(j.detail || 'Login failed')
      login(j.token, { username: j.username, role: j.role })
    } catch (x: any) { setErr(x.message) } finally { setBusy(false) }
  }
  return (
    <div className="grid h-full place-items-center grid-bg">
      <form onSubmit={submit} className="w-[360px] rounded-xl border border-line bg-panel p-7 shadow-2xl">
        <div className="mb-6 flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-lg border border-cyan-500/40 bg-cyan-500/10"><Crosshair className="text-cyan-300" size={22} /></div>
          <div><div className="text-lg font-semibold tracking-wide">AgentScope</div><div className="text-xs text-slate-500">AI agent mission control</div></div>
        </div>
        <label className="mb-1 block text-xs text-slate-400">Username</label>
        <input className={clsx(inputCls, 'mb-3')} value={u} onChange={(e) => setU(e.target.value)} autoFocus />
        <label className="mb-1 block text-xs text-slate-400">Password</label>
        <input className={clsx(inputCls, 'mb-4')} type="password" value={p} onChange={(e) => setP(e.target.value)} />
        {err && <div className="mb-3 rounded border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-xs text-red-300">{err}</div>}
        <Btn variant="primary" className="w-full justify-center py-2" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</Btn>
        <p className="mt-4 text-[11px] leading-snug text-slate-500">First run? The generated admin password is printed in the backend log and saved to <span className="mono">data/initial_admin_password.txt</span>.</p>
      </form>
    </div>
  )
}

function TopBar() {
  const { wsState, overview, alerts, adapters, agents, events, setView, user, logout, focusAgent, setFocus } = useStore()
  const running = Object.values(adapters).filter((a) => a.state === 'running').length
  const errored = Object.values(adapters).filter((a) => a.state === 'error').length
  const unacked = alerts.filter((a) => !a.acked).length
  const active = Object.values(agents).filter((a) => a.kind === 'agent' && a.status === 'working').length
  const simulated = useMemo(() => events.slice(-200).some((e) => e.evidence === 'simulated'), [events])
  const health = wsState !== 'open' ? 'bad' : errored ? 'warn' : 'ok'
  const stat = (label: string, value: React.ReactNode, onClick?: () => void, hot?: boolean) => (
    <button onClick={onClick} className="flex flex-col items-start rounded-md px-3 py-0.5 text-left hover:bg-white/5">
      <span className="text-[10px] uppercase tracking-wider text-slate-500">{label}</span>
      <span className={clsx('text-sm font-semibold tabular-nums', hot ? 'text-amber-300' : 'text-slate-100')}>{value}</span>
    </button>
  )
  return (
    <header className="flex h-12 shrink-0 items-center gap-1 border-b border-line bg-panel px-3">
      <div className="mr-3 flex items-center gap-2">
        <Crosshair className="text-cyan-300" size={18} /><span className="font-semibold tracking-wide">AgentScope</span>
      </div>
      <div className="flex items-center gap-2 rounded-md border border-line px-2.5 py-1 text-xs">
        <span className={clsx('h-2 w-2 rounded-full', health === 'ok' ? 'bg-emerald-400' : health === 'warn' ? 'bg-amber-400' : 'bg-red-500 animate-pulse')} />
        <span className="text-slate-300">{wsState !== 'open' ? 'Disconnected' : errored ? 'Degraded' : 'Healthy'}</span>
      </div>
      <div className="mx-1 h-6 w-px bg-line" />
      {stat('Active agents', active, () => setView('agents'))}
      {stat('Active operations', overview?.recent?.running_ops ?? '–', () => setView('timeline'))}
      {stat('Network (5m)', overview?.recent?.network_5m ?? '–', () => setView('network'))}
      {stat('Alerts', unacked, () => setView('alerts'), unacked > 0)}
      {stat('Monitoring', `${running} source${running === 1 ? '' : 's'}${errored ? ` · ${errored} err` : ''}`, () => setView('integrations'), errored > 0)}
      {focusAgent && (
        <button onClick={() => setFocus(null)} className="ml-2 flex items-center gap-1.5 rounded-full border border-cyan-500/50 bg-cyan-500/10 px-2.5 py-0.5 text-xs text-cyan-200">
          <Crosshair size={12} /> Focus: {agents[focusAgent]?.name?.slice(0, 22) || focusAgent.slice(-12)} <X size={12} />
        </button>
      )}
      <div className="flex-1" />
      {simulated && <span className="mr-2 rounded border border-dashed border-fuchsia-500/60 bg-fuchsia-500/10 px-2 py-0.5 text-[11px] font-semibold tracking-wider text-fuchsia-300">SIMULATED DATA PRESENT</span>}
      <span className="mr-2 text-xs text-slate-500">{user?.username} · {user?.role}</span>
      <Btn variant="ghost" onClick={logout} title="Sign out"><LogOut size={14} /></Btn>
    </header>
  )
}

function Sidebar() {
  const { view, setView, alerts } = useStore()
  const unacked = alerts.filter((a) => !a.acked).length
  return (
    <nav className="flex w-52 shrink-0 flex-col gap-0.5 border-r border-line bg-panel p-2">
      {NAV.map((n) => (
        <button key={n.id} onClick={() => setView(n.id)}
          className={clsx('flex items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-[13px] transition',
            view === n.id ? 'bg-cyan-500/12 text-cyan-200 ring-1 ring-cyan-500/30' : 'text-slate-400 hover:bg-white/5 hover:text-slate-100')}>
          <n.icon size={16} />
          <span className="flex-1">{n.label}</span>
          {n.id === 'alerts' && unacked > 0 && <span className="rounded-full bg-amber-500/25 px-1.5 text-[10px] font-semibold text-amber-300">{unacked}</span>}
        </button>
      ))}
      <div className="mt-auto rounded-md border border-line p-2 text-[10px] leading-snug text-slate-500">
        <div className="mb-1 flex items-center gap-1 text-slate-400"><Radio size={11} /> Evidence levels</div>
        Every record is tagged <b className="text-emerald-300">VERIFIED</b>, <b className="text-sky-300">REPORTED</b>, <b className="text-violet-300">EXPOSED</b>, <b className="text-amber-300">INFERRED</b> or <b className="text-fuchsia-300">SIMULATED</b>.
      </div>
    </nav>
  )
}

const VIEWS: Record<ViewId, () => JSX.Element | null> = {
  overview: Overview, map: LiveMap, agents: Agents, network: NetworkView, filesystem: Filesystem, processes: Processes, commands: Commands,
  timeline: Timeline, history: HistoryView, alerts: Alerts, integrations: Integrations, settings: Settings,
}

export default function App() {
  const { user, view, login, logout, bootstrap, connect, selection } = useStore()
  const [checking, setChecking] = useState(!!getToken())
  useEffect(() => {
    const onLogout = () => logout()
    window.addEventListener('agentscope:logout', onLogout)
    if (getToken()) {
      api<{ username: string; role: string }>('/api/auth/me')
        .then((u) => { login(getToken(), u) })
        .catch((e) => { if (!(e instanceof ApiError) || e.status !== 401) console.error(e) })
        .finally(() => setChecking(false))
    }
    return () => window.removeEventListener('agentscope:logout', onLogout)
  }, [])
  void bootstrap; void connect
  if (checking) return <div className="grid h-full place-items-center text-slate-500"><Activity className="animate-pulse" /></div>
  if (!user) return <Login />
  const View = VIEWS[view]
  const showTime = view === 'map' || view === 'timeline'
  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <main className="flex min-w-0 flex-1 flex-col">
          {showTime && <TimeControls />}
          <div className="min-h-0 flex-1"><View /></div>
        </main>
        <Inspector open={!!selection} />
      </div>
    </div>
  )
}
