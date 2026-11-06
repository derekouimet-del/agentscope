import { useEffect, useMemo, useState } from 'react'
import { Box, ChevronRight, Cpu } from 'lucide-react'
import { api } from '../api'
import { useStore } from '../store'
import { Card, Empty, EvidenceBadge, StatusPill, catOf, clsx, fmtDur, fmtTime } from '../lib'
import { AgentChip } from '../components/Parts'
import type { AEvent } from '../types'

interface Proc { pid: number; ppid: number; user: string; age: number; cmd: string }
interface PNode extends Proc { kids: PNode[] }

function tree(procs: Proc[]): PNode[] {
  const by = new Map<number, PNode>()
  procs.forEach((p) => by.set(p.pid, { ...p, kids: [] }))
  const roots: PNode[] = []
  by.forEach((n) => { const par = by.get(n.ppid); if (par && par !== n) par.kids.push(n); else roots.push(n) })
  return roots
}

function linkCommand(p: Proc, cmds: AEvent[]): AEvent | undefined {
  const c = p.cmd.trim()
  if (c.length < 8) return undefined
  return cmds.find((e) => {
    const text = e.input || e.target || ''
    return text.length > 3 && (text.includes(c) || (c.includes(text.trim()) && text.trim().length > 8))
  })
}

function PRow({ n, depth, cmds, container, open, toggle }: { n: PNode; depth: number; cmds: AEvent[]; container: string; open: Set<number>; toggle: (pid: number) => void }) {
  const select = useStore((s) => s.select)
  const sel = useStore((s) => s.selection)
  const linked = linkCommand(n, cmds)
  const isOpen = !open.has(-n.pid)   // default expanded; stored set holds collapsed pids (negated)
  return (
    <>
      <div onClick={() => select({ kind: 'process', id: `${container}:${n.pid}`, data: { ...n, container, linked } })}
        className={clsx('flex cursor-pointer items-center gap-1.5 py-1 pr-3 text-xs hover:bg-white/5', sel?.id === `${container}:${n.pid}` && 'bg-cyan-500/10')} style={{ paddingLeft: 8 + depth * 16 }}>
        {n.kids.length ? <ChevronRight size={12} onClick={(e) => { e.stopPropagation(); toggle(n.pid) }} className={clsx('shrink-0 transition', isOpen && 'rotate-90')} /> : <span className="w-3" />}
        <Cpu size={12} className="shrink-0 text-violet-300" />
        <span className="mono w-14 shrink-0 text-slate-500">{n.pid}</span>
        <span className="min-w-0 flex-1 truncate" title={n.cmd}>{n.cmd}</span>
        {linked && <span title="Matches an agent-reported command (inferred link)" className="rounded border border-dashed border-amber-500/50 px-1 text-[9px] text-amber-300">≈ agent cmd</span>}
        <span className="w-16 text-right text-[10px] text-slate-600">{n.user}</span>
        <span className="w-14 text-right text-[10px] text-slate-500">{fmtDur(n.age * 1000)}</span>
      </div>
      {isOpen && n.kids.map((k) => <PRow key={k.pid} n={k} depth={depth + 1} cmds={cmds} container={container} open={open} toggle={toggle} />)}
    </>
  )
}

export default function Processes() {
  const { select, selection, events } = useStore()
  const [data, setData] = useState<any>(null)
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  useEffect(() => { let on = true; const load = () => api('/api/processes').then((d) => on && setData(d)).catch(() => {}); load(); const t = setInterval(load, 3000); return () => { on = false; clearInterval(t) } }, [])
  const cmds: AEvent[] = useMemo(() => (data?.commands || []).filter((e: AEvent) => ['terminal', 'python'].includes(e.category)).map((e: AEvent) => ({ ...e, ts: Date.parse(e.timestamp) / 1000 })), [data])
  const spawned = useMemo(() => events.filter((e) => e.category === 'subprocess' && !e.metadata?.baseline && !e.metadata?.ended).slice(-30).reverse(), [events])
  const toggle = (pid: number) => setCollapsed((c) => { const n = new Set(c); n.has(-pid) ? n.delete(-pid) : n.add(-pid); return n })
  return (
    <div className="grid h-full grid-cols-1 gap-4 p-4 xl:grid-cols-2">
      <div className="flex min-h-0 flex-col gap-4">
        <Card title="Live process tree (verified: docker top)" className="flex min-h-0 flex-1 flex-col">
          <div className="border-b border-line px-3 py-1.5 text-[10px] leading-snug text-slate-500">Process table sampled every few seconds. Very short-lived processes can be missed; exit codes and the launching agent are not observable here. PIDs are as seen by the Docker host. Command lines are redacted.</div>
          <div className="min-h-0 flex-1 overflow-y-auto py-1">
            {!data?.containers?.length ? <Empty>No Docker monitor connected, or no monitored container. Add the Docker integration and set a container name on your Agent Zero integration.</Empty> :
              data.containers.map((c: any) => (
                <div key={c.container} className="mb-2">
                  <div onClick={() => select({ kind: 'container', id: c.container })} className="flex cursor-pointer items-center gap-2 bg-panel2/60 px-3 py-1.5 text-xs font-semibold text-emerald-300 hover:bg-panel2"><Box size={13} />{c.container}<span className="font-normal text-slate-500">· {c.processes.length} processes{c.linked_instance ? ` · hosts ${c.linked_instance}` : ''}</span></div>
                  {tree(c.processes).map((n) => <PRow key={n.pid} n={n} depth={0} cmds={cmds} container={c.container} open={collapsed} toggle={toggle} />)}
                </div>))}
          </div>
        </Card>
      </div>
      <div className="flex min-h-0 flex-col gap-4">
        <Card title="Commands executed by agents (reported)" className="flex min-h-0 flex-[3] flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto">
            {!cmds.length && <Empty>No agent commands in the last hour.</Empty>}
            {cmds.map((e) => (
              <div key={e.event_id} onClick={() => select({ kind: 'event', id: e.event_id })} className={clsx('cursor-pointer border-b border-line/60 px-3 py-2 text-xs hover:bg-white/5', selection?.id === e.event_id && 'bg-cyan-500/10')}>
                <div className="flex items-center gap-2"><span className="mono text-slate-500">{fmtTime(e.ts!)}</span><span style={{ color: catOf(e.category).color }}>{e.action.replace(/_/g, ' ')}</span>
                  <span className="text-slate-500"><AgentChip id={e.agent_id} name={e.agent_name} /></span><span className="flex-1" /><StatusPill s={e.status} /><span className="mono text-slate-500">{fmtDur(e.duration_ms)}</span></div>
                <pre className="mono mt-1 max-h-14 overflow-hidden whitespace-pre-wrap break-all text-[11px] text-slate-300">{(e.input || e.target || '').slice(0, 300)}</pre>
                {e.output && <pre className="mono mt-0.5 max-h-10 overflow-hidden whitespace-pre-wrap break-all text-[10px] text-slate-500">{e.output.slice(0, 200)}</pre>}
                <div className="mt-1 flex items-center gap-2"><EvidenceBadge e={e.evidence} /><span className="text-[10px] text-slate-600">exit code: not exposed by the framework</span></div>
              </div>))}
          </div>
        </Card>
        <Card title="Processes spawned (observed)" className="flex min-h-0 flex-[2] flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto">
            {!spawned.length && <Empty>No new processes since monitoring began.</Empty>}
            {spawned.map((e) => (
              <div key={e.event_id} onClick={() => select({ kind: 'event', id: e.event_id })} className="flex cursor-pointer items-center gap-2 border-b border-line/60 px-3 py-1.5 text-xs hover:bg-white/5">
                <span className="mono text-slate-500">{fmtTime(e.ts!)}</span><span className="mono text-slate-500">pid {e.metadata?.pid}</span><span className="min-w-0 flex-1 truncate">{e.target}</span><StatusPill s={e.status} /><span className="mono text-slate-500">{fmtDur(e.duration_ms)}</span></div>))}
          </div>
        </Card>
      </div>
    </div>
  )
}
