import { useEffect, useState } from 'react'
import { Eye, Play, Plug, Plus, Square, Trash2, Wand2 } from 'lucide-react'
import { api } from '../api'
import { useStore } from '../store'
import { Btn, Card, clsx, inputCls } from '../lib'
import type { FieldSpec, Framework, Integration } from '../types'

function Field({ f, v, set }: { f: FieldSpec; v: any; set: (v: any) => void }) {
  if (f.type === 'bool') return (
    <label className="flex items-start gap-2 text-xs"><input type="checkbox" className="mt-0.5 accent-cyan-400" checked={!!v} onChange={(e) => set(e.target.checked)} />
      <span><span className="text-slate-200">{f.label}</span>{f.help && <span className="block text-[11px] text-slate-500">{f.help}</span>}</span></label>)
  return (
    <div><label className="mb-1 block text-xs text-slate-400">{f.label}{f.required && <span className="text-red-400"> *</span>}</label>
      {f.type === 'list'
        ? <textarea rows={3} className={clsx(inputCls, 'mono text-[11px]')} value={(v || []).join('\n')} onChange={(e) => set(e.target.value.split('\n').map((x) => x.trim()).filter(Boolean))} />
        : <input className={inputCls} type={f.type === 'password' ? 'password' : f.type === 'number' ? 'number' : 'text'} step="any" autoComplete="off"
            value={v ?? ''} onChange={(e) => set(f.type === 'number' ? Number(e.target.value) : e.target.value)} placeholder={f.type === 'password' && v === '********' ? 'stored (leave to keep)' : ''} />}
      {f.help && <div className="mt-0.5 text-[11px] text-slate-600">{f.help}</div>}</div>)
}

function stateCls(s: string) { return s === 'running' ? 'bg-emerald-500/15 text-emerald-300' : s === 'error' ? 'bg-red-500/15 text-red-300' : 'bg-slate-500/20 text-slate-400' }

export default function Integrations() {
  const { user, adapters } = useStore()
  const [fw, setFw] = useState<Framework[]>([])
  const [items, setItems] = useState<Integration[]>([])
  const [edit, setEdit] = useState<{ id?: string; framework: string; name: string; config: Record<string, any>; enabled: boolean } | null>(null)
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [info, setInfo] = useState<Record<string, any>>({})
  const admin = user?.role === 'admin'
  const load = () => api<Integration[]>('/api/integrations').then(setItems)
  useEffect(() => { api<Framework[]>('/api/frameworks').then(setFw); load() }, [])
  useEffect(() => { load() }, [adapters])

  const startNew = (f: Framework) => { setTest(null); setErr(''); setEdit({ framework: f.framework, name: f.display_name, config: Object.fromEntries(f.config_schema.map((s) => [s.name, s.default])), enabled: true }) }
  const spec = edit ? fw.find((f) => f.framework === edit.framework) : undefined
  const save = async () => {
    if (!edit) return; setBusy(true); setErr('')
    try {
      const body = { framework: edit.framework, name: edit.name, config: edit.config, enabled: edit.enabled }
      if (edit.id) await api(`/api/integrations/${edit.id}`, { method: 'PUT', body }); else await api('/api/integrations', { body })
      setEdit(null); load()
    } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  const runTest = async () => {
    if (!edit) return; setBusy(true); setTest(null)
    try { setTest(await api(`/api/integrations/test${edit.id ? `?id=${edit.id}` : ''}`, { body: { framework: edit.framework, name: edit.name, config: edit.config, enabled: true } })) }
    catch (e: any) { setTest({ ok: false, message: e.message }) } finally { setBusy(false) }
  }
  const toggle = async (i: Integration) => { await api(`/api/integrations/${i.id}`, { method: 'PUT', body: { framework: i.framework, name: i.name, config: i.config, enabled: !i.enabled } }); load() }
  const remove = async (i: Integration) => { if (confirm(`Remove "${i.name}"? Recorded events are kept.`)) { await api(`/api/integrations/${i.id}`, { method: 'DELETE' }); load() } }
  const reveal = async (i: Integration) => { setInfo({ ...info, [i.id]: await api(`/api/integrations/${i.id}/connect-info`) }) }
  const sim = async (s: 'start' | 'stop' | 'clear') => { await api(`/api/simulator/${s}`, { method: 'POST', body: {} }); load() }
  const hasSim = items.some((i) => i.framework === 'simulator')

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <div className="space-y-4">
          <Card title="Configured integrations" right={<span className="text-[11px] text-slate-500">{items.length}</span>}>
            {!items.length && <div className="p-6 text-center text-sm text-slate-500">Nothing configured. Add Agent Zero to start observing a real agent.</div>}
            {items.map((i) => {
              const st = adapters[i.id] || i.status
              const f = fw.find((x) => x.framework === i.framework)
              return (
                <div key={i.id} className="border-b border-line/60 p-3">
                  <div className="flex items-center gap-3">
                    <Plug size={15} className="text-cyan-300" />
                    <div className="min-w-0 flex-1"><div className="text-sm font-medium">{i.name} <span className="text-xs font-normal text-slate-500">· {f?.display_name || i.framework}</span></div>
                      <div className="truncate text-[11px] text-slate-500">{i.id}{i.config.url ? ` · ${i.config.url}` : ''}{i.config.container ? ` · container ${i.config.container}` : ''}</div></div>
                    <span className={clsx('rounded px-2 py-0.5 text-[11px] font-semibold', stateCls(st.state))}>{st.state}</span>
                    {admin && <><Btn onClick={() => toggle(i)} title={i.enabled ? 'Disable' : 'Enable'}>{i.enabled ? <Square size={11} /> : <Play size={11} />}</Btn>
                      <Btn onClick={() => { setTest(null); setErr(''); setEdit({ id: i.id, framework: i.framework, name: i.name, config: i.config, enabled: i.enabled }) }}>Edit</Btn>
                      <Btn variant="danger" onClick={() => remove(i)}><Trash2 size={11} /></Btn></>}
                  </div>
                  {st.message && <div className={clsx('mt-1.5 text-[11px]', st.state === 'error' ? 'text-red-300' : 'text-slate-500')}>{st.message}{st.stats && Object.keys(st.stats).length > 0 && ` · ${Object.entries(st.stats).map(([k, v]) => `${k}: ${v}`).join(' · ')}`}</div>}
                  {admin && (i.framework === 'generic_http' || i.framework === 'openai_proxy') && (
                    <div className="mt-2">{info[i.id] ? <pre className="mono overflow-auto rounded border border-line bg-ink p-2 text-[11px] text-slate-300">{i.framework === 'generic_http'
                      ? `POST  ${location.origin}${info[i.id].ingest_path}\nHeader  X-AgentScope-Key: ${info[i.id].key}\nBody   [ {event}, … ]  (see docs/ADAPTERS.md)`
                      : `OPENAI_BASE_URL=${location.origin}${info[i.id].base_url_path}`}</pre>
                      : <Btn onClick={() => reveal(i)}><Eye size={11} /> Show connection details (audit-logged)</Btn>}</div>)}
                </div>)
            })}
          </Card>

          {edit && (
            <Card title={`${edit.id ? 'Edit' : 'New'} · ${spec?.display_name}`}>
              <div className="space-y-3 p-3">
                <div><label className="mb-1 block text-xs text-slate-400">Name</label><input className={inputCls} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></div>
                {spec?.config_schema.map((f) => <Field key={f.name} f={f} v={edit.config[f.name]} set={(v) => setEdit({ ...edit, config: { ...edit.config, [f.name]: v } })} />)}
                <label className="flex items-center gap-2 text-xs"><input type="checkbox" className="accent-cyan-400" checked={edit.enabled} onChange={(e) => setEdit({ ...edit, enabled: e.target.checked })} />Enabled</label>
                {test && <div className={clsx('rounded border p-2 text-xs', test.ok ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200' : 'border-red-500/40 bg-red-500/10 text-red-200')}>{test.message}</div>}
                {err && <div className="text-xs text-red-300">{err}</div>}
                <div className="flex gap-2"><Btn onClick={runTest} disabled={busy}>Test connection</Btn><Btn variant="primary" onClick={save} disabled={busy}>Save &amp; start</Btn><Btn variant="ghost" onClick={() => setEdit(null)}>Cancel</Btn></div>
                <p className="text-[11px] text-slate-600">Passwords and keys are encrypted at rest and never shown again. Monitoring is read-only.</p>
              </div>
            </Card>)}
        </div>

        <div className="space-y-4">
          <Card title="Add an integration">
            <div className="space-y-1 p-2">
              {fw.filter((f) => f.framework !== 'simulator').map((f) => (
                <div key={f.framework} className="rounded-md border border-line p-2.5">
                  <div className="flex items-center gap-2"><div className="flex-1 text-sm font-medium">{f.display_name}<span className="ml-2 rounded bg-slate-500/20 px-1.5 text-[10px] text-slate-400">{f.mode}</span></div>
                    {admin && <Btn variant="primary" onClick={() => startNew(f)}><Plus size={11} /> Add</Btn>}</div>
                  <p className="mt-1 text-[11px] text-slate-500">{f.description}</p>
                  <div className="mt-1.5 space-y-0.5 text-[10px] text-slate-600">{Object.entries(f.capabilities).map(([k, v]) => <div key={k}><b className="text-slate-500">{k.replace(/_/g, ' ')}:</b> {v}</div>)}</div>
                </div>))}
              <div className="rounded-md border border-dashed border-line p-2.5 text-[11px] text-slate-500">
                <b className="text-slate-400">OpenCode, OpenClaw, LangGraph, AutoGen, CrewAI</b> - no native adapter yet. Use <i>Generic HTTP ingest</i> (event schema + Python SDK, see docs/ADAPTERS.md) or the <i>OpenAI-compatible proxy</i>. New adapters are one file each; the engine needs no changes.
              </div>
            </div>
          </Card>
          <Card title="Demo data (simulator)">
            <div className="space-y-2 p-3 text-xs text-slate-400">
              <p>The simulator creates clearly labelled <span className="text-fuchsia-300">SIMULATED</span> multi-agent activity so you can explore the interface without a live agent. Nothing here reflects real behaviour.</p>
              {admin ? <div className="flex gap-2"><Btn variant="primary" onClick={() => sim('start')}><Wand2 size={11} /> Start</Btn><Btn onClick={() => sim('stop')} disabled={!hasSim}>Stop</Btn><Btn variant="danger" onClick={() => sim('clear')}>Stop &amp; delete simulated data</Btn></div> : <p>Admin role required.</p>}
            </div>
          </Card>
          <Card title="What AgentScope can and cannot see">
            <ul className="list-disc space-y-1.5 p-3 pl-6 text-[11px] leading-snug text-slate-400">
              <li><b className="text-sky-300">Agent Zero log</b>: tool calls and progress summaries as <i>reported</i> by the framework. Not syscalls.</li>
              <li><b className="text-emerald-300">Docker API</b>: process table, filesystem diff, interface counters, lifecycle: <i>verified</i>, but attributed to the container, not an individual agent.</li>
              <li>Hidden model reasoning, TLS payloads, DNS queries and process exit codes are <b>not observable</b>.</li>
              <li>URLs and deletions found inside scripts are shown as <b className="text-amber-300">INFERRED</b>.</li>
              <li>The Docker socket grants powerful access. Mount it read-only (Compose default) or point <i>Docker host</i> at a socket proxy that only allows GET.</li>
            </ul>
          </Card>
        </div>
      </div>
    </div>
  )
}
