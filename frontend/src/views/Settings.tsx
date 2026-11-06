import { useEffect, useState } from 'react'
import { Trash2, UserPlus } from 'lucide-react'
import { api } from '../api'
import { useStore } from '../store'
import { Btn, Card, clsx, fmtDateTime, inputCls } from '../lib'

function Toggle({ label, help, v, set, warn, disabled }: { label: string; help: string; v: boolean; set: (v: boolean) => void; warn?: boolean; disabled?: boolean }) {
  return (
    <label className="flex items-start gap-3 border-b border-line/60 p-3 text-sm">
      <input type="checkbox" disabled={disabled} className="mt-1 accent-cyan-400" checked={v} onChange={(e) => set(e.target.checked)} />
      <span><span className={clsx(warn && v && 'text-amber-300')}>{label}</span><span className="block text-xs text-slate-500">{help}</span></span>
    </label>)
}

export default function Settings() {
  const { user } = useStore()
  const admin = user?.role === 'admin'
  const [s, setS] = useState<any>(null)
  const [saved, setSaved] = useState('')
  const [users, setUsers] = useState<any[]>([])
  const [audit, setAudit] = useState<any[]>([])
  const [nu, setNu] = useState({ username: '', password: '', role: 'viewer' })
  const [err, setErr] = useState('')
  const load = () => { api('/api/settings').then(setS); if (admin) { api('/api/users').then(setUsers); api('/api/audit').then(setAudit) } }
  useEffect(load, [])
  if (!s) return null
  const put = async (patch: Record<string, any>) => { setS({ ...s, ...patch }); setS(await api('/api/settings', { method: 'PUT', body: patch })); setSaved('Saved'); setTimeout(() => setSaved(''), 1500) }
  const addUser = async () => { setErr(''); try { await api('/api/users', { body: nu }); setNu({ username: '', password: '', role: 'viewer' }); load() } catch (e: any) { setErr(e.message) } }
  const lines = (k: string) => (s[k] || []).join('\n')
  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mx-auto grid max-w-5xl gap-4">
        {!admin && <div className="rounded border border-amber-500/30 bg-amber-500/5 p-2 text-xs text-amber-200">Settings are read-only for the {user?.role} role.</div>}
        {saved && <div className="fixed right-4 top-14 rounded bg-emerald-500/20 px-3 py-1 text-xs text-emerald-200">{saved}</div>}
        <Card title="Privacy &amp; data retention">
          <div className="grid items-center gap-3 border-b border-line/60 p-3 sm:grid-cols-[1fr_200px]">
            <div><div className="text-sm">Retention (days)</div><div className="text-xs text-slate-500">Events and alerts older than this are deleted hourly. 0 = keep forever.</div></div>
            <input type="number" min={0} disabled={!admin} className={inputCls} value={s.retention_days} onChange={(e) => put({ retention_days: Number(e.target.value) })} />
          </div>
          <div className="grid items-center gap-3 border-b border-line/60 p-3 sm:grid-cols-[1fr_200px]">
            <div><div className="text-sm">IP geolocation</div><div className="text-xs text-slate-500">“ip-api.com” sends external destination IP addresses to a third party. Off by default.</div></div>
            <select disabled={!admin} className={inputCls} value={s.geoip_provider} onChange={(e) => put({ geoip_provider: e.target.value })}><option value="off">Off</option><option value="ipapi">ip-api.com (online)</option></select>
          </div>
          <Toggle label="Resolve hostnames to IPs (DNS)" help="AgentScope looks up hostnames the agent reported so scope and location can be shown. These are AgentScope's own lookups, not the agent's." v={s.resolve_dns} disabled={!admin} set={(v) => put({ resolve_dns: v })} />
          <div className="p-3"><div className="mb-1 text-sm">Extra redaction patterns (regex, one per line)</div>
            <textarea rows={3} disabled={!admin} className={clsx(inputCls, 'mono text-xs')} defaultValue={lines('extra_redaction_patterns')} onBlur={(e) => put({ extra_redaction_patterns: e.target.value.split('\n').map((x) => x.trim()).filter(Boolean) })} />
            <div className="mt-1 text-xs text-slate-500">Built-in redaction already covers API keys, bearer tokens, passwords, private keys, JWTs, cookies and URL credentials before anything is stored.</div></div>
          <div className="border-t border-line/60 p-3"><div className="mb-1 text-sm">Additional sensitive path fragments (one per line)</div>
            <textarea rows={2} disabled={!admin} className={clsx(inputCls, 'mono text-xs')} defaultValue={lines('sensitive_paths')} onBlur={(e) => put({ sensitive_paths: e.target.value.split('\n').map((x) => x.trim()).filter(Boolean) })} /></div>
        </Card>
        <Card title="Permissions (all off by default - monitoring is read-only)">
          <Toggle warn label="Allow viewing file contents from monitored containers" help="Operators can read files out of containers through the Docker API (size-capped, redacted, admin-only for sensitive paths, audit-logged)." v={s.allow_file_content_view} disabled={!admin} set={(v) => put({ allow_file_content_view: v })} />
          <Toggle warn label="Allow the TCP connection probe" help="Lets the Docker integration run `cat /proc/net/tcp` inside the container to list connections. This executes a (read-only) command in the monitored environment. Also requires the option on the Docker integration. Audit-logged." v={s.allow_exec_probe} disabled={!admin} set={(v) => put({ allow_exec_probe: v })} />
          <Toggle warn label="Allow active intervention (pause / resume agents)" help="Admin-only API that asks Agent Zero to pause or resume a chat. Every use is audit-logged." v={s.allow_intervention} disabled={!admin} set={(v) => put({ allow_intervention: v })} />
        </Card>
        {admin && (
          <Card title="Users &amp; roles">
            <div className="p-3 text-xs text-slate-500">viewer: read-only · operator: + acknowledge alerts, export, inspect files · admin: + integrations, rules, settings, users.</div>
            {users.map((u) => (
              <div key={u.username} className="flex items-center gap-3 border-t border-line/60 px-3 py-2 text-sm"><span className="flex-1">{u.username}</span><span className="rounded bg-slate-500/20 px-2 text-xs">{u.role}</span>
                {u.username !== user?.username && <Btn variant="danger" onClick={async () => { await api(`/api/users/${u.username}`, { method: 'DELETE' }); load() }}><Trash2 size={11} /></Btn>}</div>))}
            <div className="flex flex-wrap items-center gap-2 border-t border-line/60 p-3">
              <input className={inputCls + ' !w-40'} placeholder="username" value={nu.username} onChange={(e) => setNu({ ...nu, username: e.target.value })} />
              <input className={inputCls + ' !w-48'} type="password" placeholder="password (8+ chars)" value={nu.password} onChange={(e) => setNu({ ...nu, password: e.target.value })} />
              <select className={inputCls + ' !w-32'} value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}><option>viewer</option><option>operator</option><option>admin</option></select>
              <Btn variant="primary" onClick={addUser}><UserPlus size={12} /> Add / update</Btn>{err && <span className="text-xs text-red-300">{err}</span>}
            </div>
          </Card>)}
        {admin && (
          <Card title="Audit log (logins, exports, config changes, interventions)">
            <div className="max-h-72 overflow-y-auto text-xs">{audit.map((a, i) => (
              <div key={i} className="grid grid-cols-[150px_100px_130px_1fr] gap-2 border-b border-line/50 px-3 py-1.5"><span className="text-slate-500">{fmtDateTime(a.ts)}</span><span>{a.username}</span><span className="text-cyan-300">{a.action}</span><span className="truncate text-slate-500" title={a.detail}>{a.detail}</span></div>))}</div>
          </Card>)}
      </div>
    </div>
  )
}
