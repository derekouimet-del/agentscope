import { useEffect, useMemo, useState } from 'react'
import { Check, Copy, Download, Lightbulb, RefreshCw } from 'lucide-react'
import { api, download, qs } from '../api'
import { useStore } from '../store'
import { Btn, Card, Empty, EvidenceBadge, SEV_BG, StatusPill, clsx, fmtDateTime, fmtDur, inputCls, shortAgent } from '../lib'

interface Cmd {
  event_id: string; timestamp: string; agent_id: string; agent_name: string | null; tool: string | null; runtime: string
  command: string; status: string; duration_ms: number | null; output: string | null; evidence: string; source: string; risk: string
}
interface Explanation {
  kind: string; summary: string; coverage: string; disclaimer: string
  steps: { text: string; exe: string | null; description: string; operator: string | null; then?: string; flags: { flag: string; description: string | null }[]; redirects: string[]; env: string[] }[]
  warnings: { level: string; message: string }[]
}

function useCopy() {
  const [done, setDone] = useState<string | null>(null)
  const copy = (key: string, text: string) => {
    navigator.clipboard?.writeText(text).then(() => { setDone(key); setTimeout(() => setDone(null), 1400) }).catch(() => {})
  }
  return { done, copy }
}

function ExplainPanel({ cmd, onClose }: { cmd: Cmd; onClose: () => void }) {
  const [ex, setEx] = useState<Explanation | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    setEx(null); setErr('')
    api<Explanation>('/api/explain', { body: { command: cmd.command, runtime: cmd.runtime } }).then(setEx).catch((e) => setErr(e.message))
  }, [cmd.event_id])
  return (
    <Card title="Explanation" right={<button onClick={onClose} className="text-[11px] text-slate-500 hover:text-slate-200">close</button>}>
      <div className="space-y-3 p-3 text-xs">
        {err && <div className="text-red-300">{err}</div>}
        {!ex && !err && <div className="text-slate-500">Analysing…</div>}
        {ex && (<>
          <p className="text-sm leading-relaxed text-slate-200">{ex.summary}</p>
          {ex.warnings.length > 0 && (
            <div className="space-y-1">{ex.warnings.map((w, i) => (
              <div key={i} className="flex items-start gap-2"><span className={clsx('shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase', SEV_BG[w.level] || SEV_BG.info)}>{w.level}</span><span className="text-slate-300">{w.message}</span></div>))}</div>)}
          {ex.steps.length > 0 && (
            <ol className="space-y-2 border-l border-line pl-3">
              {ex.steps.map((s, i) => (
                <li key={i} className="relative">
                  <span className="absolute -left-[17px] top-0 grid h-4 w-4 place-items-center rounded-full bg-panel2 text-[9px] text-slate-400">{i + 1}</span>
                  <div className="mono break-all text-[11px] text-cyan-200">{s.text}</div>
                  <div className="mt-0.5 text-slate-300">{s.description}</div>
                  {s.flags.length > 0 && <ul className="mt-1 space-y-0.5 text-[11px] text-slate-400">{s.flags.map((f, j) => <li key={j}><span className="mono text-slate-300">{f.flag}</span>{f.description ? ` — ${f.description}` : ' — flag not in reference'}</li>)}</ul>}
                  {s.env.length > 0 && <div className="mt-0.5 text-[11px] text-slate-500">sets variables: {s.env.join(', ')}</div>}
                  {s.then && i < ex.steps.length - 1 && <div className="mt-1 text-[10px] uppercase tracking-wider text-slate-600">{s.operator}  {s.then}</div>}
                </li>))}
            </ol>)}
          {ex.coverage && <div className="text-[11px] text-slate-500">{ex.coverage}</div>}
          <div className="rounded border border-line bg-ink/60 p-2 text-[11px] leading-snug text-slate-500">
            <Lightbulb size={11} className="mr-1 inline text-amber-300" />{ex.disclaimer} The explanation is generated locally; the command never leaves this machine.
          </div>
        </>)}
      </div>
    </Card>
  )
}

export default function Commands() {
  const { user, agents, events } = useStore()
  const [rows, setRows] = useState<Cmd[]>([])
  const [q, setQ] = useState('')
  const [agent, setAgent] = useState('')
  const [range, setRange] = useState(86400)
  const [sel, setSel] = useState<Cmd | null>(null)
  const [busy, setBusy] = useState(false)
  const { done, copy } = useCopy()
  const cmdCount = useMemo(() => events.filter((e) => e.category === 'terminal' || e.category === 'python').length, [events])
  const load = () => {
    setBusy(true)
    api<{ commands: Cmd[] }>(`/api/commands${qs({ q, agent, since: Date.now() / 1000 - range, limit: 500 })}`)
      .then((r) => setRows(r.commands.slice().reverse())).finally(() => setBusy(false))
  }
  useEffect(load, [q, agent, range, cmdCount])
  const formatOne = (c: Cmd) => (c.runtime === 'python' ? `python3 - <<'PYEOF'\n${c.command}\nPYEOF` : c.command)
  const exportScript = () => download(`/api/commands/script${qs({ agent, since: Date.now() / 1000 - range })}`, 'agentscope-commands.sh').catch(() => {})
  const sel_ = { border: 'rounded-md border border-line bg-ink px-2 py-1.5 text-xs text-slate-300' }
  return (
    <div className="grid h-full grid-cols-1 gap-4 p-4 xl:grid-cols-[1fr_400px]">
      <Card title={`Commands (${rows.length})`} className="flex min-h-0 flex-col"
        right={<div className="flex gap-2">
          <Btn onClick={load} disabled={busy}><RefreshCw size={12} className={busy ? 'animate-spin' : ''} /> Refresh</Btn>
          {user?.role !== 'viewer' && <Btn variant="primary" onClick={exportScript} disabled={!rows.length} title="Download as a shell script"><Download size={12} /> Script</Btn>}
          <Btn onClick={() => copy('all', rows.map(formatOne).join('\n'))} disabled={!rows.length} title="Copy every command in order">
            {done === 'all' ? <Check size={12} /> : <Copy size={12} />} Copy all</Btn>
        </div>}>
        <div className="flex flex-wrap gap-2 border-b border-line p-2">
          <input className={inputCls + ' min-w-48 flex-1 !py-1 text-xs'} placeholder="Search commands…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select className={sel_.border} value={agent} onChange={(e) => setAgent(e.target.value)}><option value="">All agents</option>{Object.values(agents).filter((a) => a.kind === 'agent').map((a) => <option key={a.agent_id} value={a.agent_id}>{shortAgent(a.agent_id, a.name)}</option>)}</select>
          <select className={sel_.border} value={range} onChange={(e) => setRange(Number(e.target.value))}><option value={3600}>Last hour</option><option value={86400}>Last 24 h</option><option value={604800}>Last 7 days</option></select>
        </div>
        <div className="border-b border-line px-3 py-1.5 text-[10px] leading-snug text-slate-500">
          Commands exactly as the agent reported them, oldest at the bottom. Secrets were redacted before storage, so redacted values must be filled in before running. Copying a command is the only action here - nothing is executed.
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {!rows.length && <Empty>No commands recorded in this range.</Empty>}
          {rows.map((c) => (
            <div key={c.event_id} className={clsx('group border-b border-line/60 px-3 py-2 hover:bg-white/5', sel?.event_id === c.event_id && 'bg-cyan-500/10')}>
              <div className="flex items-center gap-2 text-[11px] text-slate-500">
                <span className="mono">{fmtDateTime(Date.parse(c.timestamp) / 1000).split(', ').pop()}</span>
                <span>{shortAgent(c.agent_id, c.agent_name)}</span>
                <span className="rounded bg-slate-500/20 px-1 text-[10px] text-slate-400">{c.runtime}</span>
                <span className="flex-1" /><StatusPill s={c.status} />
                <span className="mono">{fmtDur(c.duration_ms)}</span>
              </div>
              <div className="mt-1 flex items-start gap-2">
                <pre className="mono min-w-0 flex-1 cursor-pointer whitespace-pre-wrap break-all text-[12px] text-slate-100" onClick={() => setSel(c)}>{c.command}</pre>
                <button onClick={() => copy(c.event_id, formatOne(c))} title="Copy command" className="shrink-0 rounded border border-line bg-panel2 p-1 text-slate-400 hover:text-slate-100">
                  {done === c.event_id ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}</button>
                <button onClick={() => setSel(c)} title="Explain" className="shrink-0 rounded border border-line bg-panel2 p-1 text-slate-400 hover:text-amber-200"><Lightbulb size={13} /></button>
              </div>
              <div className="mt-1 flex items-center gap-2"><EvidenceBadge e={c.evidence as any} />{c.risk !== 'info' && <span className={clsx('rounded px-1 text-[10px] uppercase', SEV_BG[c.risk])}>{c.risk}</span>}</div>
            </div>))}
        </div>
      </Card>
      <div className="min-h-0 overflow-y-auto">
        {sel ? <ExplainPanel cmd={sel} onClose={() => setSel(null)} /> :
          <Card title="Explain a command"><div className="p-4 text-xs text-slate-500">Select a command to see what each part does: the program, its flags, pipes, redirects and any risks. Explanations come from a built-in reference, so they describe the command in general, not what actually happened.</div></Card>}
        {user?.role === 'viewer' && <div className="mt-3 text-[10px] text-slate-600">Viewer role: copy and explain are available; script download requires operator.</div>}
      </div>
    </div>
  )
}
