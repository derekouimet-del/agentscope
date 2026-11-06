import { useEffect, useMemo, useState } from 'react'
import { geoGraticule10, geoNaturalEarth1, geoPath } from 'd3-geo'
import { feature } from 'topojson-client'
import world from 'world-atlas/countries-110m.json'
import { Info, Lock } from 'lucide-react'
import { api } from '../api'
import { useStore } from '../store'
import { Card, EvidenceBadge, Empty, clsx, fmtAgo, fmtBytes, fmtDur, inputCls } from '../lib'

const SCOPE_META: Record<string, { label: string; color: string; help: string }> = {
  localhost: { label: 'Localhost', color: '#94a3b8', help: '127.0.0.0/8, ::1' },
  docker: { label: 'Docker network', color: '#2dd4bf', help: '172.16/12, host.docker.internal, compose service names' },
  internal: { label: 'Internal / LAN', color: '#facc15', help: 'RFC1918, link-local, .local/.lan' },
  external: { label: 'External internet', color: '#38bdf8', help: 'Public addresses and hostnames' },
  unknown: { label: 'Unknown', color: '#64748b', help: '' },
}

const land = feature(world as any, (world as any).objects.countries) as any
const projection = geoNaturalEarth1().fitSize([960, 480], { type: 'Sphere' } as any)
const pathGen = geoPath(projection)
const landPath = pathGen(land) || ''
const grid = pathGen(geoGraticule10()) || ''

function GeoMap({ dests, provider }: { dests: any[]; provider: string }) {
  const select = useStore((s) => s.select)
  const pts = dests.filter((d) => d.geo?.lat != null && d.geo?.lon != null)
  return (
    <div className="relative">
      <svg viewBox="0 0 960 480" className="w-full rounded-md bg-ink">
        <path d={grid} fill="none" stroke="#10192e" strokeWidth={0.5} />
        <path d={landPath} fill="#122038" stroke="#1d2f50" strokeWidth={0.5} />
        {pts.map((d) => {
          const [x, y] = projection([d.geo.lon, d.geo.lat]) || [0, 0]
          const c = SCOPE_META[d.scope || 'external'].color
          const r = Math.min(9, 3.5 + Math.log2(1 + d.count))
          return (
            <g key={d.key} className="cursor-pointer" onClick={() => select({ kind: 'destination', id: d.key, data: d })}>
              <circle cx={x} cy={y} r={r + 5} fill={c} opacity={0.15} className="pulse-ring" />
              <circle cx={x} cy={y} r={r} fill={c} opacity={0.9} stroke="#060a13" strokeWidth={1}><title>{d.host} · {d.geo.city || ''} {d.geo.country || ''} · {d.count}×</title></circle>
            </g>)
        })}
      </svg>
      {provider === 'off' && !pts.length && <div className="absolute inset-x-0 bottom-2 mx-auto w-fit rounded border border-line bg-panel/90 px-3 py-1 text-[11px] text-slate-400">IP geolocation is off. Enable it in Settings (this sends destination IPs to ip-api.com).</div>}
      {provider !== 'off' && !pts.length && <div className="absolute inset-x-0 bottom-2 mx-auto w-fit rounded border border-line bg-panel/90 px-3 py-1 text-[11px] text-slate-400">No external destinations with a known location yet.</div>}
    </div>
  )
}

function Spark({ data, k, color }: { data: { ts: number; rx: number; tx: number; cpu: number }[]; k: 'rx' | 'tx'; color: string }) {
  if (data.length < 2) return <div className="h-8" />
  const max = Math.max(1, ...data.map((d) => d[k]))
  const pts = data.map((d, i) => `${(i / (data.length - 1)) * 100},${30 - (d[k] / max) * 28}`).join(' ')
  return <svg viewBox="0 0 100 30" preserveAspectRatio="none" className="h-8 w-full"><polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" /></svg>
}

export default function NetworkView() {
  const { select, selection, events, metricHistory, metrics } = useStore()
  const [data, setData] = useState<any>(null)
  const [scope, setScope] = useState('')
  const [q, setQ] = useState('')
  const netCount = useMemo(() => events.filter((e) => e.metadata?.network).length, [events])
  useEffect(() => { let on = true; const load = () => api('/api/network').then((d) => on && setData(d)).catch(() => {}); load(); const t = setInterval(load, 5000); return () => { on = false; clearInterval(t) } }, [netCount])
  const dests: any[] = (data?.destinations || []).filter((d: any) => (!scope || d.scope === scope) && (!q || `${d.host} ${d.ip} ${d.port}`.toLowerCase().includes(q.toLowerCase())))
  const scopes = data?.scopes || {}
  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5 text-[11px] leading-snug text-amber-100/80">
        <Lock size={14} className="mt-0.5 shrink-0" />
        <div>{(data?.notes || []).map((n: string) => <div key={n}>{n}</div>)}</div>
      </div>
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {['localhost', 'docker', 'internal', 'external'].map((s) => (
          <button key={s} onClick={() => setScope(scope === s ? '' : s)} className={clsx('rounded-lg border bg-panel p-3 text-left', scope === s ? 'border-cyan-500/60' : 'border-line hover:border-slate-500')}>
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-slate-500"><span className="h-2 w-2 rounded-full" style={{ background: SCOPE_META[s].color }} />{SCOPE_META[s].label}</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">{scopes[s] || 0}</div><div className="text-[10px] text-slate-600">{SCOPE_META[s].help}</div>
          </button>))}
      </div>
      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Where are agents connecting? (external destinations)" className="xl:col-span-2"><div className="p-3"><GeoMap dests={dests} provider={data?.geo_provider || 'off'} /></div></Card>
        <Card title="Container traffic (verified counters)">
          {!Object.keys(metrics).length ? <Empty>No Docker monitor connected.</Empty> : Object.values(metrics).map((m) => (
            <div key={m.container} className="cursor-pointer border-b border-line/60 p-3 hover:bg-white/5" onClick={() => select({ kind: 'container', id: m.container })}>
              <div className="flex justify-between text-xs"><b className="text-emerald-300">{m.container}</b><span className="text-slate-500">total ↓{fmtBytes(m.net_rx)} ↑{fmtBytes(m.net_tx)}</span></div>
              <div className="mt-1 text-[10px] text-slate-500">receive {fmtBytes(m.rx_rate)}/s</div><Spark data={metricHistory[m.container] || []} k="rx" color="#38bdf8" />
              <div className="text-[10px] text-slate-500">transmit {fmtBytes(m.tx_rate)}/s</div><Spark data={metricHistory[m.container] || []} k="tx" color="#fb923c" />
            </div>))}
          <div className="p-3 text-[10px] text-slate-600"><Info size={10} className="mr-1 inline" />Interface byte counters only. Per-connection volumes need the opt-in TCP probe and still exclude payloads.</div>
        </Card>
        <Card title={`Destinations (${dests.length})`} className="xl:col-span-3" right={<input className={inputCls + ' !w-56 !py-1 text-xs'} placeholder="Filter host / IP / port" value={q} onChange={(e) => setQ(e.target.value)} />}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead><tr className="text-left text-[10px] uppercase tracking-wider text-slate-500">{['Host / domain', 'IP', 'Port', 'Protocol', 'Scope', 'Location', 'Seen', 'Last', 'Duration', 'Data in/out', 'Evidence'].map((h) => <th key={h} className="px-3 py-2 font-semibold">{h}</th>)}</tr></thead>
              <tbody>
                {dests.map((d) => (
                  <tr key={d.key} onClick={() => select({ kind: 'destination', id: d.key, data: d })} className={clsx('cursor-pointer border-t border-line/50 hover:bg-white/5', selection?.id === d.key && 'bg-cyan-500/10')}>
                    <td className="max-w-56 truncate px-3 py-1.5 font-medium">{d.encrypted && <Lock size={10} className="mr-1 inline text-slate-500" />}{d.host}</td>
                    <td className="mono px-3 text-slate-400">{d.ip || <span className="text-slate-600">not resolved</span>}</td>
                    <td className="px-3">{d.port ?? '—'}</td><td className="px-3 text-slate-400">{d.protocol}</td>
                    <td className="px-3"><span style={{ color: SCOPE_META[d.scope || 'unknown'].color }}>{SCOPE_META[d.scope || 'unknown'].label}</span></td>
                    <td className="px-3 text-slate-400">{d.geo ? `${d.geo.city || ''} ${d.geo.country_code || ''}` : '—'}</td>
                    <td className="px-3 tabular-nums">{d.count}{d.failed ? <span className="text-red-400"> ({d.failed}✗)</span> : ''}</td>
                    <td className="px-3 text-slate-500">{fmtAgo(Date.parse(d.last) / 1000)}</td>
                    <td className="px-3 text-slate-400">{d.duration_ms ? fmtDur(d.duration_ms) : '—'}</td>
                    <td className="px-3 text-slate-400">{d.bytes_in != null || d.bytes_out != null ? `${fmtBytes(d.bytes_in)} / ${fmtBytes(d.bytes_out)}` : 'n/a'}</td>
                    <td className="px-3"><div className="flex gap-1">{d.evidence.map((x: any) => <EvidenceBadge key={x} e={x} />)}</div></td>
                  </tr>))}
              </tbody>
            </table>
            {!dests.length && <Empty>No network activity recorded yet.</Empty>}
          </div>
        </Card>
      </div>
    </div>
  )
}
