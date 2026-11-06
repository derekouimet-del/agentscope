import { useMemo } from 'react'
import { Crosshair, GitBranch, Map as MapIcon } from 'lucide-react'
import { useStore } from '../store'
import { Btn, Card, Empty, StatusPill, agentColor, clsx, fmtAgo, shortAgent } from '../lib'
import type { Agent } from '../types'

function AgentCard({ a, depth }: { a: Agent; depth: number }) {
  const { select, selection, setFocus, focusAgent, setView, agents } = useStore()
  const color = agentColor(a.agent_id)
  const working = a.status === 'working'
  const kids = Object.values(agents).filter((x) => x.parent_agent_id === a.agent_id).length
  return (
    <div style={{ marginLeft: depth * 28 }} onClick={() => select({ kind: 'agent', id: a.agent_id })}
      className={clsx('cursor-pointer rounded-lg border bg-panel p-3 transition hover:border-slate-500', selection?.id === a.agent_id ? 'border-cyan-500/60' : 'border-line')}>
      <div className="flex items-center gap-3">
        <div className="relative grid h-10 w-10 shrink-0 place-items-center rounded-full border-2 text-xs font-bold" style={{ borderColor: color, color }}>
          {shortAgent(a.agent_id, a.name).replace(/^A\d+ \(.*?\) · /, '').slice(0, 2).toUpperCase()}
          {working && <span className="pulse-ring absolute inset-0 rounded-full border-2" style={{ borderColor: color }} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2"><span className="truncate text-sm font-semibold">{a.name}</span>
            {a.parent_agent_id && <span className="flex items-center gap-0.5 rounded bg-pink-500/15 px-1.5 text-[10px] text-pink-300"><GitBranch size={10} /> subagent</span>}
            {kids > 0 && <span className="rounded bg-slate-500/20 px-1.5 text-[10px] text-slate-300">{kids} sub</span>}
            {a.kind === 'container' && <span className="rounded bg-emerald-500/15 px-1.5 text-[10px] text-emerald-300">container</span>}
            {a.source === 'simulator' && <span className="rounded border border-dashed border-fuchsia-500/50 px-1.5 text-[10px] text-fuchsia-300">SIMULATED</span>}
          </div>
          <div className="mt-0.5 flex items-center gap-3 text-xs text-slate-400">
            <StatusPill s={working ? 'running' : 'info'} /><span>{working ? (a.current_tool ? `using ${a.current_tool}` : 'working') : 'idle'}</span>
            <span className="text-slate-600">·</span><span>{a.event_count} events</span><span className="text-slate-600">·</span><span>{fmtAgo(a.last_seen)}</span>
          </div>
          {a.current_task && <div className="mt-1 truncate text-xs text-slate-500">Task: {a.current_task}</div>}
          <div className="mt-1 flex items-center gap-3 text-[11px] text-slate-500">
            {a.model && <span>model <b className="text-slate-300">{a.model}</b></span>}
            {(a.tokens_in > 0 || a.tokens_out > 0) && <span>tokens {a.tokens_in.toLocaleString()}↓ {a.tokens_out.toLocaleString()}↑</span>}
            {a.context_pct != null && <span>context {a.context_pct.toFixed(0)}%</span>}
            {!a.model && a.kind === 'agent' && a.source === 'agent_zero' && <span>model / tokens: not exposed by Agent Zero's log</span>}
          </div>
        </div>
        <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
          <Btn variant={focusAgent === a.agent_id ? 'primary' : 'default'} onClick={() => setFocus(focusAgent === a.agent_id ? null : a.agent_id)} title="Isolate this agent's activity across the dashboard"><Crosshair size={12} /> {focusAgent === a.agent_id ? 'Isolated' : 'Isolate'}</Btn>
          <Btn onClick={() => { setFocus(a.agent_id); setView('map') }} title="Open focused map"><MapIcon size={12} /></Btn>
        </div>
      </div>
    </div>
  )
}

export default function Agents() {
  const { agents, focusAgent, setFocus } = useStore()
  const list = useMemo(() => Object.values(agents).sort((a, b) => b.last_seen - a.last_seen), [agents])
  const ordered = useMemo(() => {
    const out: { a: Agent; depth: number }[] = []
    const byParent = new Map<string | null, Agent[]>()
    list.forEach((a) => { const p = a.parent_agent_id && agents[a.parent_agent_id] ? a.parent_agent_id : null; byParent.set(p, [...(byParent.get(p) || []), a]) })
    const walk = (p: string | null, d: number) => (byParent.get(p) || []).forEach((a) => { out.push({ a, depth: d }); walk(a.agent_id, d + 1) })
    walk(null, 0)
    return out
  }, [list, agents])
  const real = ordered.filter((o) => o.a.kind === 'agent')
  const containers = ordered.filter((o) => o.a.kind !== 'agent')
  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex items-center justify-between">
        <div><h2 className="text-base font-semibold">Agents &amp; topology</h2><p className="text-xs text-slate-500">Parent-child relationships come from the framework's own agent hierarchy. “Isolate” filters the map, timeline and other views to one agent; clear it for the global view.</p></div>
        {focusAgent && <Btn variant="primary" onClick={() => setFocus(null)}>Show global view</Btn>}
      </div>
      {!real.length && <Card><Empty>No agents seen yet. Configure an integration or start the simulator.</Empty></Card>}
      <div className="space-y-2">{real.map(({ a, depth }) => <AgentCard key={a.agent_id} a={a} depth={depth} />)}</div>
      {containers.length > 0 && <><h3 className="mt-6 mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Monitored containers (system telemetry sources)</h3>
        <div className="space-y-2">{containers.map(({ a }) => <AgentCard key={a.agent_id} a={a} depth={0} />)}</div></>}
    </div>
  )
}
