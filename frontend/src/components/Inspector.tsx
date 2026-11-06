import { useEffect, useMemo, useState } from 'react'
import { X, Crosshair, Map as MapIcon, Check, FolderOpen } from 'lucide-react'
import { api, qs, ApiError } from '../api'
import { useStore } from '../store'
import { Btn, EVIDENCE, EvidenceBadge, SEV_BG, StatusPill, agentColor, catOf, clsx, fmtAgo, fmtBytes, fmtDateTime, fmtDur, fmtTime, shortAgent } from '../lib'
import type { AEvent, Alert } from '../types'
import { AgentChip, Code, EventRow, KV } from './Parts'

function EventDetail({ id }: { id: string }) {
  const { events, index, select } = useStore()
  const live = index.has(id) ? events[index.get(id)!] : undefined
  const [fetched, setFetched] = useState<AEvent | null>(null)
  const [trace, setTrace] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    setTrace(null); setFetched(null)
    if (!live) api<AEvent>(`/api/events/${encodeURIComponent(id)}`).then((e) => setFetched({ ...e, ts: Date.parse(e.timestamp) / 1000 })).catch(() => {})
  }, [id])
  const e = live || fetched
  if (!e) return <div className="p-4 text-sm text-slate-500">Loading…</div>
  const c = catOf(e.category)
  const net = e.metadata?.network
  const loadTrace = async () => { setLoading(true); try { setTrace(await api(`/api/events/${encodeURIComponent(id)}/trace`)) } finally { setLoading(false) } }
  const { network: _n, ...meta } = e.metadata || {}
  return (
    <div className="p-3">
      <div className="mb-2 flex items-start gap-2">
        <c.icon size={18} style={{ color: c.color }} className="mt-0.5" />
        <div className="min-w-0"><div className="text-sm font-semibold">{e.action.replace(/_/g, ' ')}</div><div className="break-all text-xs text-slate-400">{e.target}</div></div>
      </div>
      <div className="mb-2 rounded-md border border-line bg-ink/60 p-2 text-[11px] leading-snug text-slate-400">
        <EvidenceBadge e={e.evidence} full /> <span className="ml-1">{EVIDENCE[e.evidence]?.help}</span>
      </div>
      <KV k="Time">{fmtDateTime(e.ts!)}<span className="text-slate-500"> · {fmtAgo(e.ts!)}</span></KV>
      <KV k="Agent"><button className="hover:underline" onClick={() => select({ kind: 'agent', id: e.agent_id })}><AgentChip id={e.agent_id} name={e.agent_name} /></button></KV>
      <KV k="Parent agent">{e.parent_agent_id && <button className="hover:underline" onClick={() => select({ kind: 'agent', id: e.parent_agent_id! })}><AgentChip id={e.parent_agent_id} /></button>}</KV>
      <KV k="Type">{e.event_type} / {e.category}</KV>
      <KV k="Tool">{e.tool}</KV>
      <KV k="Status"><StatusPill s={e.status} /></KV>
      <KV k="Duration">{e.duration_ms !== null ? fmtDur(e.duration_ms) : undefined}</KV>
      <KV k="Risk">{e.risk !== 'info' ? e.risk : undefined}</KV>
      <KV k="Source">{e.source}{e.instance_id ? ` · ${e.instance_id}` : ''}</KV>
      <KV k="Task">{e.task_id && <button className="mono text-cyan-300 hover:underline" onClick={() => select({ kind: 'event', id: e.task_id! })}>{e.task_id.slice(-24)}</button>}</KV>
      <KV k="Event ID" mono>{e.event_id}</KV>
      {net && (
        <div className="my-2 rounded-md border border-line p-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Network metadata</div>
          <KV k="Host" mono>{net.host}</KV><KV k="IP" mono>{net.ip}</KV><KV k="Port">{net.port}</KV><KV k="Protocol">{net.protocol}</KV>
          <KV k="Scope">{net.scope}</KV><KV k="Direction">{net.direction}</KV><KV k="TCP state">{net.state}</KV>
          <KV k="Location">{net.geo ? `${net.geo.city || ''} ${net.geo.country || ''} (${net.geo.provider})` : undefined}</KV>
          <KV k="Visibility"><span className="text-amber-200">{net.visibility === 'reported_url_only' ? 'URL reported by the agent tool. HTTPS contents (headers, body, response) are NOT observable.' : net.visibility === 'connection_metadata' ? 'Connection metadata only (no payload).' : net.visibility}</span></KV>
          <KV k="IP source">{net.ip_source}</KV>
        </div>
      )}
      {e.input && <Code label="Input">{e.input}</Code>}
      {e.output && <Code label={e.evidence === 'exposed_reasoning' ? 'Exposed reasoning (as logged by the framework)' : 'Output'}>{e.output}</Code>}
      {Object.keys(meta).length > 0 && <Code label="Metadata" max={200}>{JSON.stringify(meta, null, 2)}</Code>}

      <div className="mt-3 border-t border-line pt-3">
        <Btn variant="primary" onClick={loadTrace} disabled={loading}>{loading ? 'Reconstructing…' : 'Reconstruct what led to this'}</Btn>
        {trace && <Trace t={trace} />}
      </div>
    </div>
  )
}

function Trace({ t }: { t: any }) {
  const select = useStore((s) => s.select)
  const norm = (e: any): AEvent => ({ ...e, ts: Date.parse(e.timestamp) / 1000 })
  const Sec = ({ title, items, note }: { title: string; items: any[]; note?: string }) => items?.length ? (
    <div className="mt-3"><div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">{title}</div>
      {note && <div className="mb-1 text-[10px] text-slate-600">{note}</div>}
      <div className="overflow-hidden rounded-md border border-line">{items.map((e) => <EventRow key={e.event_id} compact e={norm(e)} onClick={() => select({ kind: 'event', id: e.event_id })} />)}</div></div>
  ) : null
  const taskEvents = (t.task_events || []).filter((e: any) => !['reasoning'].includes(e.category)).slice(-25)
  return (
    <div>
      <Sec title="Causal chain (parent events)" items={t.ancestors} />
      <Sec title="Delegation context" items={t.delegation_context} note="Latest delegation/task events of the parent agent before this event." />
      <Sec title={`Task sequence up to this event (${taskEvents.length})`} items={taskEvents} note="Events in the same task, oldest first." />
      {!t.task_events?.length && <Sec title="Preceding events by this agent" items={t.agent_history.slice(-15)} />}
      <Sec title="Derived from this event" items={t.children} note="Events produced by or inferred from this one." />
      <Sec title="What happened next" items={t.next} />
    </div>
  )
}

function AgentDetail({ id }: { id: string }) {
  const { agents, events, setFocus, setView, select, focusAgent } = useStore()
  const a = agents[id]
  const evs = useMemo(() => events.filter((e) => e.agent_id === id).slice(-40).reverse(), [events, id])
  if (!a) return <div className="p-4 text-sm text-slate-500">Unknown agent</div>
  const children = Object.values(agents).filter((x) => x.parent_agent_id === id)
  const ctxLimit = a.context_pct
  return (
    <div className="p-3">
      <div className="mb-3 flex items-center gap-3">
        <div className="grid h-11 w-11 place-items-center rounded-full border-2 text-sm font-bold" style={{ borderColor: agentColor(id), color: agentColor(id) }}>{shortAgent(id, a.name).slice(0, 2).toUpperCase()}</div>
        <div className="min-w-0"><div className="truncate text-sm font-semibold">{a.name}</div>
          <div className="flex items-center gap-2 text-xs"><StatusPill s={a.status === 'working' ? 'running' : 'info'} /><span className="text-slate-500">{a.status}</span></div></div>
      </div>
      <div className="mb-3 flex gap-2">
        <Btn variant={focusAgent === id ? 'primary' : 'default'} onClick={() => setFocus(focusAgent === id ? null : id)}><Crosshair size={12} />{focusAgent === id ? 'Clear focus' : 'Isolate'}</Btn>
        <Btn onClick={() => setView('map')}><MapIcon size={12} /> Map</Btn>
      </div>
      <KV k="Kind">{a.kind}</KV><KV k="Source">{a.source}</KV><KV k="Instance">{a.instance_id}</KV>
      <KV k="Current task">{a.current_task}</KV><KV k="Current tool">{a.current_tool}</KV>
      <KV k="Model">{a.model}</KV>
      <KV k="Tokens">{a.tokens_in || a.tokens_out ? `${a.tokens_in.toLocaleString()} in / ${a.tokens_out.toLocaleString()} out` : undefined}</KV>
      <KV k="Context">{ctxLimit !== null && ctxLimit !== undefined ? <div className="h-1.5 w-full rounded bg-line"><div className="h-full rounded bg-cyan-400" style={{ width: `${Math.min(100, ctxLimit)}%` }} /></div> : undefined}</KV>
      <KV k="Events">{a.event_count}</KV><KV k="First seen">{fmtDateTime(a.first_seen)}</KV><KV k="Last seen">{fmtAgo(a.last_seen)}</KV>
      <KV k="Parent">{a.parent_agent_id && <button className="hover:underline" onClick={() => select({ kind: 'agent', id: a.parent_agent_id! })}><AgentChip id={a.parent_agent_id} /></button>}</KV>
      {children.length > 0 && <KV k="Subagents">{children.map((c) => <div key={c.agent_id}><button className="hover:underline" onClick={() => select({ kind: 'agent', id: c.agent_id })}><AgentChip id={c.agent_id} name={c.name} /></button></div>)}</KV>}
      {Object.keys(a.metadata || {}).length > 0 && <Code label="Metadata">{JSON.stringify(a.metadata, null, 2)}</Code>}
      <div className="mt-3 mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Recent activity</div>
      <div className="overflow-hidden rounded-md border border-line">{evs.map((e) => <EventRow key={e.event_id} compact e={e} onClick={() => select({ kind: 'event', id: e.event_id })} />)}</div>
    </div>
  )
}

function EventList({ list, title }: { list: AEvent[]; title: string }) {
  const select = useStore((s) => s.select)
  return <>
    <div className="mt-3 mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">{title}</div>
    <div className="overflow-hidden rounded-md border border-line">{list.slice(-50).reverse().map((e) => <EventRow key={e.event_id} compact e={e} onClick={() => select({ kind: 'event', id: e.event_id })} />)}</div>
    {!list.length && <div className="text-xs text-slate-500">No events in buffer.</div>}
  </>
}

function ResourceDetail({ data }: { data: any }) {
  const events = useStore((s) => s.events)
  const list = useMemo(() => events.filter((e) => (data.eventIds as Set<string>)?.has?.(e.event_id) || data.eventIdList?.includes(e.event_id)), [events, data])
  const c = catOf(data.category)
  const net = [...list].reverse().find((e) => e.metadata?.network)?.metadata.network
  return (
    <div className="p-3">
      <div className="mb-2 flex items-center gap-2"><c.icon size={18} style={{ color: c.color }} /><div className="min-w-0"><div className="truncate text-sm font-semibold">{data.label}</div><div className="text-xs text-slate-500">{c.label} node</div></div></div>
      <KV k="Operations">{list.length}</KV>
      <KV k="Evidence">{[...new Set(list.map((e) => e.evidence))].map((x) => <span key={x} className="mr-1"><EvidenceBadge e={x} /></span>)}</KV>
      <KV k="Agents">{[...new Set(list.map((e) => e.agent_id))].map((x) => <div key={x}><AgentChip id={x} /></div>)}</KV>
      {net && <><KV k="IP" mono>{net.ip}</KV><KV k="Scope">{net.scope}</KV><KV k="Port">{net.port}</KV><KV k="Protocol">{net.protocol}</KV>
        <KV k="Location">{net.geo ? `${net.geo.city || ''} ${net.geo.country || ''}` : undefined}</KV>
        {net.encrypted && <div className="my-1 rounded border border-amber-500/30 bg-amber-500/10 p-1.5 text-[11px] text-amber-200">Encrypted (HTTPS). Only the destination is known; request/response contents are not observable.</div>}</>}
      <EventList list={list} title="Operations" />
    </div>
  )
}

function EdgeDetail({ data }: { data: any }) {
  const events = useStore((s) => s.events)
  const list = useMemo(() => events.filter((e) => data.eventIdList?.includes(e.event_id)), [events, data])
  const c = catOf(data.category)
  return (
    <div className="p-3">
      <div className="mb-2 flex items-center gap-2"><c.icon size={18} style={{ color: c.color }} /><div className="text-sm font-semibold">{data.sourceLabel} → {data.targetLabel}</div></div>
      <KV k="Operation">{c.label}</KV><KV k="Direction">{data.direction}</KV><KV k="Count">{list.length}</KV>
      <KV k="Evidence">{[...new Set(list.map((e) => e.evidence))].map((x) => <span key={x} className="mr-1"><EvidenceBadge e={x} /></span>)}</KV>
      <EventList list={list} title="Operations on this connection" />
    </div>
  )
}

function AlertDetail({ id }: { id: string }) {
  const { alerts, select, refreshAlerts, user } = useStore()
  const a = alerts.find((x) => x.id === id) as Alert | undefined
  if (!a) return null
  const ack = async () => { await api(`/api/alerts/ack${qs({ id })}`, { method: 'POST', body: {} }); refreshAlerts() }
  return (
    <div className="p-3">
      <span className={clsx('rounded px-1.5 py-0.5 text-[10px] font-bold uppercase', SEV_BG[a.severity])}>{a.severity}</span>
      <div className="mt-2 text-sm font-semibold">{a.title}</div>
      <p className="mt-1 text-xs text-slate-400">{a.description}</p>
      <div className="my-2 rounded-md border border-line bg-ink/60 p-2 text-[11px] text-slate-400">
        Triggered by an event with evidence level <EvidenceBadge e={a.data.evidence} />. Alerts describe observable activity - not intent.
      </div>
      <KV k="Rule">{a.data.rule_name}</KV><KV k="Agent"><AgentChip id={a.agent_id} /></KV><KV k="First">{fmtDateTime(a.ts)}</KV><KV k="Last">{fmtAgo(a.last_ts)}</KV><KV k="Occurrences">{a.count}</KV>
      <div className="mt-3 flex gap-2">
        <Btn onClick={() => select({ kind: 'event', id: a.event_id })}>Open source event</Btn>
        {!a.acked && user?.role !== 'viewer' && <Btn variant="primary" onClick={ack}><Check size={12} /> Acknowledge</Btn>}
      </div>
    </div>
  )
}

function DestinationDetail({ data }: { data: any }) {
  const events = useStore((s) => s.events)
  const list = useMemo(() => events.filter((e) => { const n = e.metadata?.network; return n && (n.host || n.ip) === data.host && String(n.port) === String(data.port) }), [events, data])
  return (
    <div className="p-3">
      <div className="mb-2 text-sm font-semibold">{data.host}<span className="text-slate-500">:{data.port ?? '—'}</span></div>
      <KV k="IP" mono>{data.ip}</KV><KV k="Resolved">{data.resolved_ips?.join(', ')}</KV><KV k="Protocol">{data.protocol}</KV><KV k="Scope">{data.scope}</KV>
      <KV k="Direction">{data.directions?.join(', ')}</KV>
      <KV k="Location">{data.geo ? `${data.geo.city || ''} ${data.geo.country || ''} · ${data.geo.isp || ''} (${data.geo.provider})` : 'Geolocation off or unavailable'}</KV>
      <KV k="Connections">{data.count}</KV><KV k="First seen">{data.first && fmtDateTime(Date.parse(data.first) / 1000)}</KV>
      <KV k="Total duration">{data.duration_ms ? fmtDur(data.duration_ms) : undefined}</KV><KV k="Data in">{data.bytes_in != null ? fmtBytes(data.bytes_in) : undefined}</KV><KV k="Data out">{data.bytes_out != null ? fmtBytes(data.bytes_out) : undefined}</KV>
      <KV k="Evidence">{data.evidence?.map((x: any) => <span key={x} className="mr-1"><EvidenceBadge e={x} /></span>)}</KV>
      <KV k="Visibility">{data.visibility?.map((v: string) => <div key={v} className="text-amber-200">{v === 'reported_url_only' ? 'URL reported by agent tool; TLS payload not observable' : v === 'connection_metadata' ? 'Socket metadata only; no payload' : v}</div>)}</KV>
      <KV k="Agents">{data.agents?.map((x: string) => <div key={x}><AgentChip id={x} /></div>)}</KV>
      <EventList list={list} title="Related events" />
    </div>
  )
}

function ProcessDetail({ data }: { data: any }) {
  const select = useStore((s) => s.select)
  return (
    <div className="p-3">
      <div className="mb-2 text-sm font-semibold">PID {data.pid}</div>
      <KV k="Parent PID">{data.ppid}</KV><KV k="User">{data.user}</KV><KV k="Running for">{data.age != null ? fmtDur(data.age * 1000) : undefined}</KV>
      <KV k="Container">{data.container}</KV>
      <div className="my-2 rounded-md border border-line bg-ink/60 p-2 text-[11px] text-slate-400">Sampled from <span className="mono">docker top</span> (verified). The PID namespace is the Docker host's. Exit codes and which agent launched it are not observable here.</div>
      <Code label="Command line (secrets redacted)">{data.cmd}</Code>
      {data.linked && <><div className="mt-2 text-[10px] font-semibold uppercase tracking-wider text-amber-300">Possible link to agent command (INFERRED)</div>
        <EventRow compact e={data.linked} onClick={() => select({ kind: 'event', id: data.linked.event_id })} /></>}
    </div>
  )
}

function FileDetail({ data }: { data: any }) {
  const { overview, user } = useStore()
  const containers = Object.keys(overview?.containers || {})
  const [container, setContainer] = useState(data.containers?.[0] || containers[0] || '')
  const [info, setInfo] = useState<any>(null)
  const [err, setErr] = useState('')
  useEffect(() => { setInfo(null); setErr('') }, [data.path])
  const inspect = async (content: boolean) => {
    setErr('')
    try { setInfo(await api(`/api/fs/inspect${qs({ container, path: data.path, content })}`)) } catch (e) { setErr(e instanceof ApiError ? e.message : String(e)) }
  }
  const events = useStore((s) => s.events)
  const list = useMemo(() => events.filter((e) => e.target === data.path && e.category?.startsWith('file')), [events, data.path])
  return (
    <div className="p-3">
      <div className="mb-2 break-all text-sm font-semibold">{data.path}</div>
      <KV k="Reads">{data.reads}</KV><KV k="Writes">{data.writes}</KV><KV k="Created">{data.creates}</KV><KV k="Deleted">{data.deletes}</KV>
      <KV k="Last activity">{fmtAgo(Date.parse(data.last) / 1000)}</KV><KV k="Last known size">{data.size != null ? fmtBytes(data.size) : undefined}</KV>
      <KV k="Agents">{data.agents.map((x: string) => <div key={x}><AgentChip id={x} /></div>)}</KV>
      <KV k="Evidence">{data.evidence.map((x: any) => <span key={x} className="mr-1"><EvidenceBadge e={x} /></span>)}</KV>
      {user?.role !== 'viewer' && (
        <div className="mt-2 rounded-md border border-line p-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Inspect in container (read-only, via Docker API)</div>
          <div className="flex gap-2">
            <select className="flex-1 rounded border border-line bg-ink px-2 py-1 text-xs" value={container} onChange={(e) => setContainer(e.target.value)}>
              {[...new Set([...(data.containers || []), ...containers])].map((c) => <option key={c}>{c}</option>)}</select>
            <Btn onClick={() => inspect(false)} disabled={!container}><FolderOpen size={12} /> Metadata</Btn>
            <Btn onClick={() => inspect(true)} disabled={!container}>View content</Btn>
          </div>
          {err && <div className="mt-1 text-[11px] text-red-300">{err}</div>}
          {info?.stat && <div className="mt-2"><KV k="Size">{fmtBytes(info.stat.size)}</KV><KV k="Modified">{info.stat.mtime}</KV><KV k="Mode" mono>{String(info.stat.mode)}</KV><KV k="Directory">{String(info.stat.is_dir)}</KV></div>}
          {info?.file && (info.file.binary ? <div className="text-xs text-slate-500">Binary file ({fmtBytes(info.file.size)}), not displayed.</div> : <Code label={`Content${info.file.truncated ? ' (truncated)' : ''} - secrets redacted`} max={320}>{info.file.content || ''}</Code>)}
        </div>
      )}
      <EventList list={list} title="Operations" />
    </div>
  )
}

function ContainerDetail({ id }: { id: string }) {
  const m = useStore((s) => s.metrics[id])
  if (!m) return <div className="p-3 text-sm text-slate-500">No metrics.</div>
  return (
    <div className="p-3"><div className="mb-2 text-sm font-semibold">{id}</div>
      <KV k="Status">{m.status}</KV><KV k="Image">{m.image}</KV><KV k="CPU">{m.cpu_pct}%</KV><KV k="Memory">{fmtBytes(m.mem_bytes)} / {fmtBytes(m.mem_limit)}</KV>
      <KV k="PIDs">{m.pids}</KV><KV k="Net RX/TX total">{fmtBytes(m.net_rx)} / {fmtBytes(m.net_tx)}</KV><KV k="Rate">{fmtBytes(m.rx_rate)}/s ↓ · {fmtBytes(m.tx_rate)}/s ↑</KV>
      <KV k="Linked instance">{m.linked_instance}</KV>
      <div className="mt-2 text-[11px] text-slate-500">Counters from the Docker stats API (verified). Updated {fmtTime(m.ts)}.</div></div>
  )
}

export default function Inspector({ open }: { open: boolean }) {
  const { selection, select } = useStore()
  if (!open || !selection) {
    return (
      <aside className="hidden w-[340px] shrink-0 flex-col items-center justify-center gap-2 border-l border-line bg-panel p-6 text-center text-xs text-slate-600 xl:flex">
        <Crosshair size={22} className="opacity-50" />Select an agent, event, node, connection, process or file to inspect it here.
      </aside>
    )
  }
  const titles: Record<string, string> = { agent: 'Agent', event: 'Event record', resource: 'Resource', edge: 'Connection', process: 'Process', destination: 'Destination', file: 'File', alert: 'Alert', container: 'Container' }
  return (
    <aside className="flex w-[380px] shrink-0 flex-col border-l border-line bg-panel">
      <header className="flex items-center justify-between border-b border-line px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">{titles[selection.kind]}</span>
        <button onClick={() => select(null)} className="text-slate-500 hover:text-slate-200"><X size={15} /></button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto" key={selection.kind + selection.id}>
        {selection.kind === 'event' && <EventDetail id={selection.id} />}
        {selection.kind === 'agent' && <AgentDetail id={selection.id} />}
        {selection.kind === 'resource' && <ResourceDetail data={selection.data} />}
        {selection.kind === 'edge' && <EdgeDetail data={selection.data} />}
        {selection.kind === 'alert' && <AlertDetail id={selection.id} />}
        {selection.kind === 'destination' && <DestinationDetail data={selection.data} />}
        {selection.kind === 'process' && <ProcessDetail data={selection.data} />}
        {selection.kind === 'file' && <FileDetail data={selection.data} />}
        {selection.kind === 'container' && <ContainerDetail id={selection.id} />}
      </div>
    </aside>
  )
}
