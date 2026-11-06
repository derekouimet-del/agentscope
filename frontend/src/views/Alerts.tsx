import { useEffect, useState } from 'react'
import { Check, Plus, Save, Trash2 } from 'lucide-react'
import { api, qs } from '../api'
import { useStore } from '../store'
import { Btn, Card, Empty, SEV_BG, clsx, fmtAgo, inputCls } from '../lib'
import { AgentChip } from '../components/Parts'

interface Rule { id: string; name: string; kind: string; enabled: boolean; severity: string; params: Record<string, any>; description: string }
const SEVS = ['info', 'low', 'medium', 'high', 'critical']
const KINDS = ['new_external_domain', 'sensitive_path', 'unexpected_executable', 'mass_file_creation', 'file_delete', 'unusual_port', 'subprocess_burst', 'restricted_resource', 'error_burst']

function RuleRow({ r, onSave, onDelete, canEdit }: { r: Rule; onSave: (r: Rule) => void; onDelete: () => void; canEdit: boolean }) {
  const [d, setD] = useState(r)
  const [params, setParams] = useState(JSON.stringify(r.params))
  const [bad, setBad] = useState(false)
  useEffect(() => { setD(r); setParams(JSON.stringify(r.params)) }, [r])
  const dirty = JSON.stringify(d) !== JSON.stringify(r) || params !== JSON.stringify(r.params)
  return (
    <div className="border-b border-line/60 p-3">
      <div className="flex items-center gap-3">
        <input type="checkbox" disabled={!canEdit} checked={d.enabled} onChange={(e) => { const n = { ...d, enabled: e.target.checked }; setD(n); onSave(n) }} className="accent-cyan-400" />
        <div className="min-w-0 flex-1"><div className="text-sm font-medium">{d.name}</div><div className="text-[11px] text-slate-500">{d.description}</div></div>
        <select disabled={!canEdit} value={d.severity} onChange={(e) => setD({ ...d, severity: e.target.value })} className={clsx('rounded border border-line px-1.5 py-1 text-xs', SEV_BG[d.severity])}>
          {SEVS.map((s) => <option key={s} className="bg-panel text-slate-200">{s}</option>)}</select>
      </div>
      {Object.keys(r.params).length > 0 && (
        <div className="mt-2 flex items-start gap-2">
          <textarea disabled={!canEdit} rows={Math.min(4, 1 + Math.floor(params.length / 90))} value={params} onChange={(e) => { setParams(e.target.value); try { JSON.parse(e.target.value); setBad(false) } catch { setBad(true) } }}
            className={clsx(inputCls, 'mono flex-1 text-[11px]', bad && 'border-red-500')} spellCheck={false} />
        </div>
      )}
      {canEdit && <div className="mt-2 flex gap-2">
        <Btn variant="primary" disabled={!dirty || bad} onClick={() => onSave({ ...d, params: JSON.parse(params) })}><Save size={12} /> Save</Btn>
        {r.id.startsWith('custom-') && <Btn variant="danger" onClick={onDelete}><Trash2 size={12} /> Delete</Btn>}
      </div>}
    </div>
  )
}

export default function Alerts() {
  const { alerts, refreshAlerts, select, user } = useStore()
  const [rules, setRules] = useState<Rule[]>([])
  const [tab, setTab] = useState<'alerts' | 'rules'>('alerts')
  const [showAcked, setShowAcked] = useState(false)
  const loadRules = () => api<Rule[]>('/api/rules').then(setRules)
  useEffect(() => { loadRules(); refreshAlerts() }, [])
  const canEdit = user?.role === 'admin'
  const save = async (r: Rule) => { await api(`/api/rules/${r.id}`, { method: 'PUT', body: r }); loadRules() }
  const list = alerts.filter((a) => showAcked || !a.acked)
  const ack = async (id?: string) => { await api(`/api/alerts/ack${qs({ id })}`, { method: 'POST', body: {} }); refreshAlerts() }
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-line px-4 py-2">
        {(['alerts', 'rules'] as const).map((t) => <button key={t} onClick={() => setTab(t)} className={clsx('rounded-md px-3 py-1 text-xs font-medium capitalize', tab === t ? 'bg-cyan-500/15 text-cyan-200' : 'text-slate-400 hover:text-slate-100')}>{t === 'alerts' ? `Alerts (${alerts.filter((a) => !a.acked).length} open)` : 'Rules & severity'}</button>)}
        <div className="flex-1" />
        {tab === 'alerts' && <><label className="flex items-center gap-1.5 text-xs text-slate-400"><input type="checkbox" className="accent-cyan-400" checked={showAcked} onChange={(e) => setShowAcked(e.target.checked)} />show acknowledged</label>
          {user?.role !== 'viewer' && <Btn onClick={() => ack()}><Check size={12} /> Acknowledge all</Btn>}</>}
        {tab === 'rules' && canEdit && <Btn variant="primary" onClick={async () => { await api('/api/rules', { method: 'POST', body: { name: 'Custom restricted resource', kind: 'restricted_resource', severity: 'high', enabled: true, params: { patterns: ['*.example-blocked.com'] }, description: 'Custom deny-list (glob patterns on target / host / tool).' } }); loadRules() }}><Plus size={12} /> Add deny-list rule</Btn>}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {tab === 'alerts' && <Card>
          <div className="border-b border-line px-3 py-2 text-[11px] text-slate-500">Alerts are generated from <b>observable activity</b> only, and show the evidence level of the triggering event. They do not assert intent. Alerts fire for live activity - imported history never alerts.</div>
          {!list.length && <Empty>No alerts. Rules are listed under “Rules &amp; severity”.</Empty>}
          {list.map((a) => (
            <div key={a.id} onClick={() => select({ kind: 'alert', id: a.id })} className="flex cursor-pointer items-center gap-3 border-b border-line/60 px-3 py-2.5 hover:bg-white/5">
              <span className={clsx('w-16 rounded px-1.5 py-0.5 text-center text-[10px] font-bold uppercase', SEV_BG[a.severity])}>{a.severity}</span>
              <div className="min-w-0 flex-1"><div className={clsx('truncate text-sm', a.acked && 'text-slate-500')}>{a.title}</div>
                <div className="flex items-center gap-2 text-[11px] text-slate-500"><AgentChip id={a.agent_id} /> · {fmtAgo(a.last_ts)} · evidence: {a.data.evidence}{a.count > 1 && <span className="text-amber-300"> · ×{a.count}</span>}</div></div>
              {!a.acked && user?.role !== 'viewer' && <Btn onClick={() => ack(a.id)}><Check size={12} /> Ack</Btn>}
            </div>))}
        </Card>}
        {tab === 'rules' && <Card title="Alert rules">
          {!canEdit && <div className="border-b border-line px-3 py-2 text-[11px] text-amber-300">Admin role required to edit rules.</div>}
          {rules.map((r) => <RuleRow key={r.id} r={r} canEdit={canEdit} onSave={save} onDelete={async () => { await api(`/api/rules/${r.id}`, { method: 'DELETE' }); loadRules() }} />)}
          <div className="p-3 text-[11px] text-slate-500">Rule kinds: {KINDS.join(', ')}.</div>
        </Card>}
      </div>
    </div>
  )
}
