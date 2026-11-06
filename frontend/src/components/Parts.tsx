import { useMemo, useState } from 'react'
import { Search, ChevronRight } from 'lucide-react'
import { useStore } from '../store'
import { CATS, EvidenceBadge, StatusPill, agentColor, catOf, clsx, fmtDur, fmtTime, inputCls, RISK_CLS, shortAgent, statusAt } from '../lib'
import type { AEvent } from '../types'

export function KV({ k, children, mono }: { k: string; children: React.ReactNode; mono?: boolean }) {
  if (children === null || children === undefined || children === '') return null
  return (
    <div className="grid grid-cols-[96px_1fr] gap-2 py-1 text-xs">
      <div className="text-slate-500">{k}</div>
      <div className={clsx('min-w-0 break-words text-slate-200', mono && 'mono')}>{children}</div>
    </div>
  )
}

export function Code({ children, label, max = 260 }: { children: string; label?: string; max?: number }) {
  const [open, setOpen] = useState(false)
  const long = children.length > 600
  return (
    <div className="my-1.5">
      {label && <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">{label}</div>}
      <pre className="mono overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-ink p-2 text-[11px] leading-snug text-slate-300"
        style={{ maxHeight: open ? 600 : max }}>{children}</pre>
      {long && <button onClick={() => setOpen(!open)} className="mt-0.5 text-[10px] text-cyan-400 hover:underline">{open ? 'collapse' : 'expand'}</button>}
    </div>
  )
}

export function AgentChip({ id, name }: { id: string; name?: string | null }) {
  const agents = useStore((s) => s.agents)
  const a = agents[id]
  return (
    <span className="inline-flex max-w-full items-center gap-1.5">
      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: agentColor(id) }} />
      <span className="truncate">{shortAgent(id, name || a?.name)}</span>
    </span>
  )
}

export function EventRow({ e, onClick, active, compact, cursor }: { e: AEvent; onClick?: () => void; active?: boolean; compact?: boolean; cursor?: number | null }) {
  const c = catOf(e.category)
  const Icon = c.icon
  return (
    <button onClick={onClick} className={clsx('row-new flex w-full items-center gap-2 border-b border-line/60 px-2 py-1.5 text-left text-xs hover:bg-white/5', active && 'bg-cyan-500/10')}>
      <span className="mono w-[62px] shrink-0 text-slate-500">{fmtTime(e.ts!)}</span>
      <Icon size={13} style={{ color: c.color }} className="shrink-0" />
      <span className="min-w-0 flex-1 truncate"><span className="text-slate-300">{e.action.replace(/_/g, ' ')}</span><span className="text-slate-500"> · {e.target || '—'}</span></span>
      {!compact && <EvidenceBadge e={e.evidence} />}
      <StatusPill s={statusAt(e, cursor ?? null)} />
    </button>
  )
}

export interface Filters { q: string; agent: string; category: string; status: string; evidence: string; risk: string; source: string }
export const emptyFilters: Filters = { q: '', agent: '', category: '', status: '', evidence: '', risk: '', source: '' }

export function applyFilters(events: AEvent[], f: Filters): AEvent[] {
  const q = f.q.trim().toLowerCase()
  return events.filter((e) => {
    if (f.agent && e.agent_id !== f.agent) return false
    if (f.category && e.category !== f.category) return false
    if (f.evidence && e.evidence !== f.evidence) return false
    if (f.risk && e.risk !== f.risk) return false
    if (f.source && e.source !== f.source) return false
    if (f.status === 'success' && !['completed', 'info'].includes(e.status)) return false
    if (f.status === 'failure' && !['failed', 'blocked'].includes(e.status)) return false
    if (f.status === 'running' && e.status !== 'running') return false
    if (q) {
      const hay = `${e.action} ${e.target} ${e.tool} ${e.agent_name} ${e.input ?? ''} ${e.output ?? ''} ${e.metadata?.network?.ip ?? ''}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
}

export function FilterBar({ f, set, extra }: { f: Filters; set: (f: Filters) => void; extra?: React.ReactNode }) {
  const agents = useStore((s) => s.agents)
  const sel = 'rounded-md border border-line bg-ink px-2 py-1.5 text-xs text-slate-300'
  const u = (k: keyof Filters) => (e: React.ChangeEvent<HTMLSelectElement | HTMLInputElement>) => set({ ...f, [k]: e.target.value })
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative min-w-52 flex-1"><Search size={13} className="absolute left-2 top-2.5 text-slate-500" />
        <input className={clsx(inputCls, 'pl-7')} placeholder="Search target, command, domain, IP, path, output…" value={f.q} onChange={u('q')} /></div>
      <select className={sel} value={f.agent} onChange={u('agent')}><option value="">All agents</option>
        {Object.values(agents).map((a) => <option key={a.agent_id} value={a.agent_id}>{shortAgent(a.agent_id, a.name)}</option>)}</select>
      <select className={sel} value={f.category} onChange={u('category')}><option value="">All types</option>
        {Object.entries(CATS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
      <select className={sel} value={f.status} onChange={u('status')}><option value="">Any status</option><option value="success">Success</option><option value="failure">Failure</option><option value="running">Running</option></select>
      <select className={sel} value={f.evidence} onChange={u('evidence')}><option value="">Any evidence</option>
        <option value="execution">Verified</option><option value="tool_call">Reported</option><option value="exposed_reasoning">Exposed</option><option value="inferred">Inferred</option><option value="simulated">Simulated</option></select>
      <select className={sel} value={f.risk} onChange={u('risk')}><option value="">Any risk</option>
        {['info', 'low', 'medium', 'high', 'critical'].map((r) => <option key={r}>{r}</option>)}</select>
      {extra}
    </div>
  )
}

export function EventTable({ events, cursor, groupByTask, limit = 400 }: { events: AEvent[]; cursor?: number | null; groupByTask?: boolean; limit?: number }) {
  const { select, selection, agents } = useStore()
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const rows = useMemo(() => events.slice(-limit).reverse(), [events, limit])
  const tasks = useMemo(() => {
    const t: Record<string, string> = {}
    for (const e of events) if (e.action === 'task_received' && e.task_id) t[e.task_id] = e.target || ''
    return t
  }, [events])
  const head = 'sticky top-0 z-10 bg-panel text-[10px] uppercase tracking-wider text-slate-500'

  const Row = ({ e }: { e: AEvent }) => (
    <tr key={e.event_id} onClick={() => select({ kind: 'event', id: e.event_id })}
      className={clsx('row-new cursor-pointer border-b border-line/50 text-xs hover:bg-white/5', selection?.id === e.event_id && 'bg-cyan-500/10')}>
      <td className="mono whitespace-nowrap px-2 py-1.5 text-slate-500">{fmtTime(e.ts!, true)}</td>
      <td className="max-w-40 px-2"><AgentChip id={e.agent_id} name={e.agent_name || agents[e.agent_id]?.name} /></td>
      <td className="whitespace-nowrap px-2"><span style={{ color: catOf(e.category).color }}>{e.action.replace(/_/g, ' ')}</span></td>
      <td className="px-2 text-slate-400">{e.tool || '—'}</td>
      <td className="max-w-md truncate px-2 text-slate-300" title={e.target || ''}>{e.target || '—'}</td>
      <td className="px-2"><StatusPill s={statusAt(e, cursor ?? null)} /></td>
      <td className="mono px-2 text-right text-slate-400">{fmtDur(e.duration_ms)}</td>
      <td className="px-2"><EvidenceBadge e={e.evidence} /></td>
      <td className={clsx('px-2 text-[11px]', RISK_CLS[e.risk])}>{e.risk === 'info' ? '' : e.risk}</td>
      <td className="max-w-44 truncate px-2 text-[11px] text-slate-500" title={tasks[e.task_id || ''] || ''}>{e.task_id ? (tasks[e.task_id] || e.task_id.slice(-8)) : ''}</td>
    </tr>
  )

  let groups: [string, AEvent[]][] = []
  if (groupByTask) {
    const m = new Map<string, AEvent[]>()
    for (const e of rows) { const k = e.task_id || '(no task)'; if (!m.has(k)) m.set(k, []); m.get(k)!.push(e) }
    groups = [...m.entries()]
  }
  return (
    <div className="h-full overflow-auto">
      <table className="w-full border-collapse">
        <thead><tr className={head}>
          {['Time', 'Agent', 'Action', 'Tool', 'Target', 'Status', 'Duration', 'Evidence', 'Risk', 'Task'].map((h) => <th key={h} className={clsx('px-2 py-2 text-left font-semibold', h === 'Duration' && 'text-right')}>{h}</th>)}
        </tr></thead>
        <tbody>
          {groupByTask ? groups.map(([tid, evs]) => (
            <GroupRows key={tid} tid={tid} title={tasks[tid] || (tid === '(no task)' ? 'No associated task' : tid)} evs={evs}
              collapsed={!!collapsed[tid]} toggle={() => setCollapsed({ ...collapsed, [tid]: !collapsed[tid] })} Row={Row} />
          )) : rows.map((e) => <Row key={e.event_id} e={e} />)}
        </tbody>
      </table>
      {!rows.length && <div className="p-10 text-center text-sm text-slate-500">No events match.</div>}
    </div>
  )
}

function GroupRows({ tid, title, evs, collapsed, toggle, Row }: { tid: string; title: string; evs: AEvent[]; collapsed: boolean; toggle: () => void; Row: (p: { e: AEvent }) => JSX.Element }) {
  return (
    <>
      <tr key={'h' + tid} onClick={toggle} className="cursor-pointer bg-panel2/70 text-xs hover:bg-panel2">
        <td colSpan={10} className="px-2 py-1.5"><ChevronRight size={12} className={clsx('mr-1 inline transition', !collapsed && 'rotate-90')} />
          <span className="font-semibold text-amber-200">Task</span> <span className="text-slate-300">{title.slice(0, 140)}</span> <span className="text-slate-500">· {evs.length} events</span></td>
      </tr>
      {!collapsed && evs.map((e) => <Row key={e.event_id} e={e} />)}
    </>
  )
}
