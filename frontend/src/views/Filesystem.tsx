import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, File, Folder, FolderOpen } from 'lucide-react'
import { api } from '../api'
import { useStore } from '../store'
import { Card, Empty, EvidenceBadge, catOf, clsx, fmtAgo, fmtBytes, inputCls } from '../lib'
import { EventRow } from '../components/Parts'

interface FileRec { path: string; reads: number; writes: number; creates: number; deletes: number; last: string; size: number | null; agents: string[]; evidence: any[]; containers: string[] }
interface TNode { name: string; path: string; children: Map<string, TNode>; file?: FileRec; reads: number; writes: number; creates: number; deletes: number; last: string }

function buildTree(files: FileRec[]): TNode {
  const root: TNode = { name: '/', path: '/', children: new Map(), reads: 0, writes: 0, creates: 0, deletes: 0, last: '' }
  for (const f of files) {
    const parts = f.path.replace(/\\/g, '/').split('/').filter(Boolean)
    let cur = root
    const chain = [root]
    parts.forEach((p, i) => {
      if (!cur.children.has(p)) cur.children.set(p, { name: p, path: '/' + parts.slice(0, i + 1).join('/'), children: new Map(), reads: 0, writes: 0, creates: 0, deletes: 0, last: '' })
      cur = cur.children.get(p)!
      chain.push(cur)
    })
    cur.file = f
    for (const n of chain) { n.reads += f.reads; n.writes += f.writes; n.creates += f.creates; n.deletes += f.deletes; if (f.last > n.last) n.last = f.last }
  }
  return root
}

function Row({ n, depth, open, toggle, onPick, picked }: { n: TNode; depth: number; open: Set<string>; toggle: (p: string) => void; onPick: (f: FileRec) => void; picked?: string }) {
  const isDir = n.children.size > 0
  const isOpen = open.has(n.path)
  const kids = [...n.children.values()].sort((a, b) => Number(b.children.size > 0) - Number(a.children.size > 0) || a.name.localeCompare(b.name))
  return (
    <>
      <div onClick={() => (isDir ? toggle(n.path) : n.file && onPick(n.file))}
        className={clsx('flex cursor-pointer items-center gap-1.5 py-1 pr-3 text-xs hover:bg-white/5', picked === n.path && 'bg-cyan-500/10')} style={{ paddingLeft: 8 + depth * 14 }}>
        {isDir ? <ChevronRight size={12} className={clsx('shrink-0 transition', isOpen && 'rotate-90')} /> : <span className="w-3" />}
        {isDir ? (isOpen ? <FolderOpen size={14} className="shrink-0 text-amber-300" /> : <Folder size={14} className="shrink-0 text-amber-300/80" />) : <File size={13} className="shrink-0 text-slate-400" />}
        <span className={clsx('min-w-0 flex-1 truncate', n.deletes && !isDir && 'text-red-300 line-through')}>{n.name}</span>
        <span className="flex gap-1.5 text-[10px] tabular-nums">
          {n.reads > 0 && <span style={{ color: catOf('file_read').color }}>R{n.reads}</span>}
          {n.writes + n.creates > 0 && <span style={{ color: catOf('file_write').color }}>W{n.writes + n.creates}</span>}
          {n.deletes > 0 && <span style={{ color: catOf('file_delete').color }}>D{n.deletes}</span>}
        </span>
        {n.file?.size != null && <span className="w-14 text-right text-[10px] text-slate-500">{fmtBytes(n.file.size)}</span>}
        <span className="w-14 text-right text-[10px] text-slate-600">{n.last ? fmtAgo(Date.parse(n.last) / 1000) : ''}</span>
      </div>
      {isDir && isOpen && kids.map((k) => <Row key={k.path} n={k} depth={depth + 1} open={open} toggle={toggle} onPick={onPick} picked={picked} />)}
    </>
  )
}

export default function Filesystem() {
  const { events, select, selection } = useStore()
  const [files, setFiles] = useState<FileRec[]>([])
  const [open, setOpen] = useState<Set<string>>(new Set(['/']))
  const [q, setQ] = useState('')
  const [onlyKind, setKind] = useState('')
  const fsCount = useMemo(() => events.filter((e) => e.category?.startsWith('file')).length, [events])
  useEffect(() => { api<{ files: FileRec[] }>('/api/fs/files').then((r) => setFiles(r.files)).catch(() => {}) }, [fsCount])
  const filtered = useMemo(() => files.filter((f) => (!q || f.path.toLowerCase().includes(q.toLowerCase())) &&
    (!onlyKind || (onlyKind === 'read' ? f.reads : onlyKind === 'write' ? f.writes + f.creates : f.deletes) > 0)), [files, q, onlyKind])
  const tree = useMemo(() => buildTree(filtered), [filtered])
  useEffect(() => { if (q) setOpen(new Set(['/', ...filtered.flatMap((f) => { const p = f.path.split('/').filter(Boolean); return p.map((_, i) => '/' + p.slice(0, i + 1).join('/')) })])) }, [q])
  const toggle = (p: string) => setOpen((o) => { const n = new Set(o); n.has(p) ? n.delete(p) : n.add(p); return n })
  const feed = useMemo(() => events.filter((e) => e.category?.startsWith('file')).slice(-60).reverse(), [events])
  const totals = { r: files.filter((f) => f.reads).length, w: files.filter((f) => f.writes + f.creates).length, d: files.filter((f) => f.deletes).length }
  return (
    <div className="grid h-full grid-cols-1 gap-4 p-4 xl:grid-cols-[1.2fr_1fr]">
      <Card title="Files touched by agents" className="flex min-h-0 flex-col"
        right={<div className="flex items-center gap-2 text-[11px] text-slate-500"><span style={{ color: catOf('file_read').color }}>{totals.r} read</span><span style={{ color: catOf('file_write').color }}>{totals.w} written</span><span style={{ color: catOf('file_delete').color }}>{totals.d} deleted</span></div>}>
        <div className="flex gap-2 border-b border-line p-2">
          <input className={inputCls + ' !py-1 text-xs'} placeholder="Filter by path…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={onlyKind} onChange={(e) => setKind(e.target.value)} className="rounded border border-line bg-ink px-2 text-xs"><option value="">All</option><option value="read">Read</option><option value="write">Written / created</option><option value="delete">Deleted</option></select>
        </div>
        <div className="border-b border-line px-3 py-1.5 text-[10px] leading-snug text-slate-500">The tree is built from observed paths only (agent-reported file tool calls = <EvidenceBadge e="tool_call" /> and Docker filesystem diffs = <EvidenceBadge e="execution" />). Directories never touched are not listed. Docker diffs identify the container, not the agent.</div>
        <div className="min-h-0 flex-1 overflow-y-auto py-1">
          {!files.length ? <Empty>No file activity recorded yet.</Empty> : [...tree.children.values()].map((n) => <Row key={n.path} n={n} depth={0} open={open} toggle={toggle} onPick={(f) => select({ kind: 'file', id: f.path, data: f })} picked={selection?.kind === 'file' ? selection.id : undefined} />)}
        </div>
      </Card>
      <Card title="Live file operations" className="flex min-h-0 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto">
          {feed.map((e) => <EventRow key={e.event_id} e={e} onClick={() => select({ kind: 'event', id: e.event_id })} />)}
          {!feed.length && <Empty>Waiting for file events…</Empty>}
        </div>
      </Card>
    </div>
  )
}
