import { useMemo, useState } from 'react'
import { useStore, visibleEvents } from '../store'
import { EventTable, FilterBar, applyFilters, emptyFilters, type Filters } from '../components/Parts'
import { clsx } from '../lib'

export default function Timeline() {
  const { events, cursor, focusAgent } = useStore()
  const [f, setF] = useState<Filters>(emptyFilters)
  const [group, setGroup] = useState(false)
  const vis = useMemo(() => visibleEvents({ events, cursor, focusAgent }), [events, cursor, focusAgent])
  const filtered = useMemo(() => applyFilters(vis, f), [vis, f])
  return (
    <div className="flex h-full flex-col">
      <div className="space-y-2 border-b border-line p-3">
        <FilterBar f={f} set={setF} extra={
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-slate-400"><input type="checkbox" checked={group} onChange={(e) => setGroup(e.target.checked)} className="accent-cyan-400" /> Group by task</label>} />
        <div className="text-[11px] text-slate-500">
          {filtered.length.toLocaleString()} of {vis.length.toLocaleString()} buffered events{cursor !== null && <span className={clsx('ml-2 text-amber-300')}>· viewing history (paused / rewound)</span>}
          {focusAgent && <span className="ml-2 text-cyan-300">· focused on one agent</span>}
          <span className="ml-2">· click a row for the full execution record and sequence reconstruction</span>
        </div>
      </div>
      <div className="min-h-0 flex-1"><EventTable events={filtered} cursor={cursor} groupByTask={group} limit={600} /></div>
    </div>
  )
}
