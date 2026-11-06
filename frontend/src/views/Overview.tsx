import { useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, Bot, Box, CircleDot, Globe, Play, Rocket } from 'lucide-react'
import { api } from '../api'
import { useStore, nowSec } from '../store'
import { Btn, Card, Empty, EvidenceBadge, SEV_BG, StatusPill, agentColor, catOf, clsx, fmtAgo, fmtBytes, fmtDur, fmtTime, shortAgent } from '../lib'
import { EventRow } from '../components/Parts'

function Kpi({ icon: Icon, label, value, sub, hot, onClick }: { icon: any; label: string; value: React.ReactNode; sub?: string; hot?: boolean; onClick?: () => void }) {
  return (
    <button onClick={onClick} className="rounded-lg border border-line bg-panel p-3 text-left hover:border-slate-500">
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-slate-500"><Icon size={13} />{label}</div>
      <div className={clsx('mt-1 text-2xl font-semibold tabular-nums', hot ? 'text-amber-300' : 'text-slate-100')}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </button>
  )
}

export default function Overview() {
  const { events, agents, alerts, overview, metrics, select, setView, setCursor, user, adapters, tick, refreshOverview } = useStore()
  void tick
  const [busy, setBusy] = useState(false)
  useEffect(() => { const t = setInterval(refreshOverview, 5000); return () => clearInterval(t) }, [])
  const list = useMemo(() => Object.values(agents).filter((a) => a.kind === 'agent').sort((a, b) => (a.status === 'working' ? -1 : 1) - (b.status === 'working' ? -1 : 1) || b.last_seen - a.last_seen), [agents])
  const active = list.filter((a) => a.status === 'working')
  const now = nowSec()
  const recent = events.filter((e) => e.ts! > now - 300)
  const fails = recent.filter((e) => e.status === 'failed').length
  const net = recent.filter((e) => e.metadata?.network).length
  const live = useMemo(() => events.filter((e) => e.category !== 'reasoning' && e.event_type !== 'agent_lifecycle').slice(-14).reverse(), [events])
  const unacked = alerts.filter((a) => !a.acked)
  const lastTask = useMemo(() => {
    const t = [...events].reverse().find((e) => e.action === 'task_received')
    if (!t) return null
    const steps = events.filter((e) => e.task_id === t.task_id && !['agent_lifecycle', 'reasoning'].includes(e.event_type) && e.action !== 'task_received')
    return { t, steps: steps.slice(-12), done: steps.some((s) => s.action === 'task_completed') }
  }, [events])
  const conts = Object.values(metrics)
  const nothing = !events.length && !Object.keys(adapters).length
  const startSim = async () => { setBusy(true); try { await api('/api/simulator/start', { method: 'POST', body: {} }) } finally { setBusy(false) } }

  return (
    <div className="h-full overflow-y-auto p-4">
      {nothing && (
        <div className="mb-4 flex items-center gap-4 rounded-lg border border-cyan-500/30 bg-cyan-500/5 p-4">
          <Rocket className="shrink-0 text-cyan-300" />
          <div className="flex-1 text-sm"><div className="font-semibold text-cyan-100">Welcome to AgentScope</div>
            <div className="text-xs text-slate-400">Nothing is connected yet. Connect Agent Zero (and the Docker monitor for verified process / file / network telemetry) under Integrations, or explore the interface with clearly-labelled simulated data.</div></div>
          <Btn variant="primary" onClick={() => setView('integrations')}>Connect Agent Zero</Btn>
          {user?.role === 'admin' && <Btn onClick={startSim} disabled={busy}><Play size={12} /> Start simulator</Btn>}
        </div>
      )}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi icon={Bot} label="Agents running" value={active.length} sub={`${list.length} known`} onClick={() => setView('agents')} />
        <Kpi icon={Activity} label="Events (5 min)" value={recent.length} sub={`${events.length.toLocaleString()} buffered`} onClick={() => setView('timeline')} />
        <Kpi icon={AlertTriangle} label="Failures (5 min)" value={fails} hot={fails > 0} onClick={() => setView('history')} />
        <Kpi icon={Globe} label="Network (5 min)" value={net} sub="destinations touched" onClick={() => setView('network')} />
        <Kpi icon={AlertTriangle} label="Open alerts" value={unacked.length} hot={unacked.length > 0} onClick={() => setView('alerts')} />
        <Kpi icon={Box} label="Containers" value={conts.length} sub={conts.length ? 'verified telemetry' : 'no Docker monitor'} onClick={() => setView('processes')} />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Which agents are running, and what are they doing now?" className="xl:col-span-2">
          {!list.length ? <Empty>No agents seen yet.</Empty> : list.slice(0, 8).map((a) => {
            const last = [...events].reverse().find((e) => e.agent_id === a.agent_id && e.event_type !== 'agent_lifecycle')
            return (
              <div key={a.agent_id} onClick={() => select({ kind: 'agent', id: a.agent_id })} className="flex cursor-pointer items-center gap-3 border-b border-line/60 px-3 py-2.5 hover:bg-white/5">
                <span className="relative h-3 w-3 rounded-full" style={{ background: agentColor(a.agent_id) }}>{a.status === 'working' && <span className="pulse-ring absolute inset-0 rounded-full" style={{ background: agentColor(a.agent_id) }} />}</span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{shortAgent(a.agent_id, a.name)}{a.parent_agent_id && <span className="ml-2 text-[10px] text-pink-300">subagent</span>}</div>
                  <div className="truncate text-xs text-slate-500">{last ? `${last.action.replace(/_/g, ' ')} · ${last.target || ''}` : 'no activity'}</div>
                </div>
                <StatusPill s={a.status === 'working' ? 'running' : 'info'} />
                <span className="w-16 text-right text-[11px] text-slate-500">{fmtAgo(a.last_seen)}</span>
              </div>)
          })}
        </Card>

        <Card title="Time travel">
          <div className="space-y-2 p-3 text-xs text-slate-400">
            <div>What is happening <b className="text-slate-200">right now</b>, and what happened earlier?</div>
            <div className="flex flex-wrap gap-2">
              <Btn variant="primary" onClick={() => { setCursor(null); setView('map') }}>Now · live map</Btn>
              <Btn onClick={() => { setCursor(nowSec() - 300); setView('map') }}>5 minutes ago</Btn>
              <Btn onClick={() => { setCursor(nowSec() - 1800); setView('map') }}>30 minutes ago</Btn>
              <Btn onClick={() => { setCursor(nowSec() - 300); setView('timeline') }}>Timeline −5m</Btn>
            </div>
            <div className="text-[11px] text-slate-600">Use pause, rewind and replay on the map or timeline.</div>
          </div>
        </Card>

        <Card title="Latest task journey" className="xl:col-span-2">
          {!lastTask ? <Empty>No task has been observed yet.</Empty> : (
            <div className="p-3">
              <div className="mb-2 flex items-center gap-2 text-sm"><CircleDot size={14} className="text-amber-300" /><span className="font-medium">{(lastTask.t.target || '').slice(0, 160)}</span>
                <span className={clsx('ml-auto text-xs', lastTask.done ? 'text-emerald-400' : 'text-cyan-300')}>{lastTask.done ? 'completed' : 'in progress'}</span></div>
              <ol className="relative ml-1.5 border-l border-line pl-4">
                {lastTask.steps.map((e) => { const c = catOf(e.category); return (
                  <li key={e.event_id} className="relative mb-1.5 cursor-pointer text-xs hover:text-white" onClick={() => select({ kind: 'event', id: e.event_id })}>
                    <span className="absolute -left-[21px] top-1 h-2 w-2 rounded-full" style={{ background: c.color }} />
                    <span className="mono mr-2 text-slate-600">{fmtTime(e.ts!)}</span><span style={{ color: c.color }}>{e.action.replace(/_/g, ' ')}</span><span className="text-slate-500"> · {(e.target || '').slice(0, 70)}</span>
                    <span className="ml-2"><EvidenceBadge e={e.evidence} /></span><span className="ml-2 text-slate-600">{fmtDur(e.duration_ms)}</span></li>) })}
              </ol>
              <div className="mt-2"><Btn onClick={() => select({ kind: 'event', id: lastTask.t.event_id })}>Inspect task</Btn></div>
            </div>)}
        </Card>

        <Card title="Containers (verified)" right={<button className="text-[11px] text-cyan-400" onClick={() => setView('processes')}>details</button>}>
          {!conts.length ? <Empty>Add the Docker monitor integration to see CPU, memory and traffic.</Empty> : conts.map((m) => (
            <div key={m.container} onClick={() => select({ kind: 'container', id: m.container })} className="cursor-pointer border-b border-line/60 px-3 py-2 text-xs hover:bg-white/5">
              <div className="flex justify-between"><span className="font-medium text-emerald-300">{m.container}</span><span className="text-slate-500">{m.status}</span></div>
              <div className="mt-1 grid grid-cols-3 text-[11px] text-slate-400"><span>CPU {m.cpu_pct.toFixed(1)}%</span><span>Mem {fmtBytes(m.mem_bytes)}</span><span>↓ {fmtBytes(m.rx_rate)}/s ↑ {fmtBytes(m.tx_rate)}/s</span></div>
            </div>))}
        </Card>

        <Card title="Live feed" className="xl:col-span-3">
          {live.map((e) => <EventRow key={e.event_id} e={e} onClick={() => select({ kind: 'event', id: e.event_id })} />)}
          {!live.length && <Empty>Waiting for events…</Empty>}
        </Card>
        {unacked.length > 0 && (
          <Card title="Open alerts" className="xl:col-span-3">
            {unacked.slice(0, 5).map((a) => (
              <div key={a.id} onClick={() => select({ kind: 'alert', id: a.id })} className="flex cursor-pointer items-center gap-3 border-b border-line/60 px-3 py-2 text-xs hover:bg-white/5">
                <span className={clsx('rounded px-1.5 py-0.5 text-[10px] font-bold uppercase', SEV_BG[a.severity])}>{a.severity}</span><span className="flex-1 truncate">{a.title}</span><span className="text-slate-500">{fmtAgo(a.last_ts)}</span></div>))}
          </Card>)}
      </div>
    </div>
  )
}
