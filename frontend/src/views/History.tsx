import { useEffect, useState } from 'react'
import { Download, Search } from 'lucide-react'
import { api, download, qs } from '../api'
import { useStore } from '../store'
import { Btn, CATS, inputCls, shortAgent } from '../lib'
import { EventTable } from '../components/Parts'
import type { AEvent } from '../types'

const RANGES: [string, number][] = [['Last 15 minutes', 900], ['Last hour', 3600], ['Last 6 hours', 21600], ['Last 24 hours', 86400], ['Last 7 days', 604800], ['All time', 0], ['Custom…', -1]]
const PAGE = 300

export default function HistoryView() {
  const { agents, user } = useStore()
  const [p, setP] = useState<Record<string, string>>({ range: '3600' })
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [rows, setRows] = useState<AEvent[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setP({ ...p, [k]: e.target.value })

  const params = () => {
    const r = Number(p.range)
    const since = r > 0 ? Date.now() / 1000 - r : r === -1 && from ? new Date(from).getTime() / 1000 : undefined
    const until = r === -1 && to ? new Date(to).getTime() / 1000 : undefined
    const { range, ...rest } = p
    return { ...rest, since, until }
  }
  const run = async (pg = 0) => {
    setBusy(true); setErr('')
    try {
      const r = await api<{ events: AEvent[]; total: number }>(`/api/events${qs({ ...params(), limit: PAGE, offset: pg * PAGE })}`)
      setRows(r.events.map((e) => ({ ...e, ts: Date.parse(e.timestamp) / 1000 })).reverse()); setTotal(r.total); setPage(pg)
    } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  useEffect(() => { run(0) }, [])
  const exp = (fmt: 'json' | 'csv') => download(`/api/export${qs({ ...params(), format: fmt })}`, `agentscope-events.${fmt}`).catch((e) => setErr(e.message))
  const sel = 'rounded-md border border-line bg-ink px-2 py-1.5 text-xs text-slate-300'
  const canExport = user?.role !== 'viewer'
  return (
    <div className="flex h-full flex-col">
      <div className="space-y-2 border-b border-line p-3">
        <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); run(0) }}>
          <div className="relative min-w-60 flex-1"><Search size={13} className="absolute left-2 top-2.5 text-slate-500" />
            <input className={inputCls + ' pl-7'} placeholder="Full-text search across all recorded activity…" value={p.q || ''} onChange={set('q')} /></div>
          <select className={sel} value={p.range} onChange={set('range')}>{RANGES.map(([l, v]) => <option key={v} value={v}>{l}</option>)}</select>
          {p.range === '-1' && <><input type="datetime-local" className={sel} value={from} onChange={(e) => setFrom(e.target.value)} /><input type="datetime-local" className={sel} value={to} onChange={(e) => setTo(e.target.value)} /></>}
          <Btn variant="primary" disabled={busy}>{busy ? 'Searching…' : 'Search'}</Btn>
        </form>
        <div className="flex flex-wrap items-center gap-2">
          <select className={sel} value={p.agent || ''} onChange={set('agent')}><option value="">All agents</option>{Object.values(agents).map((a) => <option key={a.agent_id} value={a.agent_id}>{shortAgent(a.agent_id, a.name)}</option>)}</select>
          <select className={sel} value={p.category || ''} onChange={set('category')}><option value="">All event types</option>{Object.entries(CATS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
          <input className={sel + ' w-32'} placeholder="Tool" value={p.tool || ''} onChange={set('tool')} />
          <input className={sel + ' w-40'} placeholder="Domain / destination" value={p.destination || ''} onChange={set('destination')} />
          <input className={sel + ' w-32'} placeholder="IP address" value={p.ip || ''} onChange={set('ip')} />
          <input className={sel + ' w-40'} placeholder="File path" value={p.path || ''} onChange={set('path')} />
          <input className={sel + ' w-32'} placeholder="Process / command" value={p.process || ''} onChange={set('process')} />
          <select className={sel} value={p.status || ''} onChange={set('status')}><option value="">Success or failure</option><option value="success">Success</option><option value="failure">Failure</option><option value="running">Running</option></select>
          <select className={sel} value={p.risk || ''} onChange={set('risk')}><option value="">Any risk</option>{['info', 'low', 'medium', 'high', 'critical'].map((r) => <option key={r}>{r}</option>)}</select>
          <select className={sel} value={p.evidence || ''} onChange={set('evidence')}><option value="">Any evidence</option><option value="execution">Verified</option><option value="tool_call">Reported</option><option value="exposed_reasoning">Exposed</option><option value="inferred">Inferred</option><option value="simulated">Simulated</option></select>
          <div className="flex-1" />
          <Btn disabled={!canExport} onClick={() => exp('json')} title={canExport ? '' : 'Operator role required'}><Download size={12} /> JSON</Btn>
          <Btn disabled={!canExport} onClick={() => exp('csv')}><Download size={12} /> CSV</Btn>
        </div>
        {err && <div className="text-xs text-red-300">{err}</div>}
        <div className="flex items-center gap-3 text-[11px] text-slate-500">
          <span>{total.toLocaleString()} matching events (stored in SQLite; export applies the same filters)</span>
          <Btn variant="ghost" disabled={page === 0} onClick={() => run(page - 1)}>← newer</Btn>
          <span>page {page + 1} / {Math.max(1, Math.ceil(total / PAGE))}</span>
          <Btn variant="ghost" disabled={(page + 1) * PAGE >= total} onClick={() => run(page + 1)}>older →</Btn>
        </div>
      </div>
      <div className="min-h-0 flex-1"><EventTable events={rows} limit={PAGE} /></div>
    </div>
  )
}
