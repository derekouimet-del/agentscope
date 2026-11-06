import { useMemo } from 'react'
import { Pause, Play, Radio, Rewind, SkipBack } from 'lucide-react'
import { useStore, nowSec } from '../store'
import { Btn, clsx, fmtDateTime, fmtTime } from '../lib'

/** Live / pause / rewind / replay scrubber shared by the Live Map and Timeline. */
export default function TimeControls() {
  const { cursor, setCursor, playing, setPlaying, speed, setSpeed, events, tick } = useStore()
  void tick
  const t0 = useMemo(() => (events.length ? events[0].ts! : nowSec() - 3600), [events.length ? events[0].event_id : ''])
  const now = nowSec()
  const live = cursor === null
  const value = live ? now : cursor!
  const span = Math.max(60, now - t0)
  const jump = (sec: number) => { setCursor(Math.max(t0, (live ? now : cursor!) - sec)); setPlaying(false) }
  return (
    <div className="flex items-center gap-3 border-b border-line bg-panel/70 px-3 py-1.5 text-xs">
      <Btn variant={live ? 'primary' : 'default'} onClick={() => { setCursor(null); setPlaying(false) }} title="Follow the live stream">
        <Radio size={13} className={live ? 'animate-pulse' : ''} /> {live ? 'LIVE' : 'Go live'}
      </Btn>
      <Btn onClick={() => { if (live) setCursor(now); else setCursor(null) }} title={live ? 'Freeze the view at this moment' : 'Resume live'}>
        {live ? <><Pause size={13} /> Pause</> : <><Play size={13} /> Resume</>}
      </Btn>
      <Btn onClick={() => jump(30)} title="Rewind 30 seconds"><Rewind size={13} /> 30s</Btn>
      <Btn onClick={() => jump(300)} title="Rewind 5 minutes"><SkipBack size={13} /> 5m</Btn>
      <Btn disabled={live} onClick={() => setPlaying(!playing)} title="Replay from this point">
        {playing ? <><Pause size={13} /> Stop replay</> : <><Play size={13} /> Replay</>}
      </Btn>
      <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="rounded border border-line bg-panel2 px-1.5 py-1">
        {[1, 2, 5, 10, 30, 60].map((s) => <option key={s} value={s}>{s}×</option>)}
      </select>
      <input type="range" className="h-1 flex-1 accent-cyan-400" min={t0} max={now} step={1} value={value}
        onChange={(e) => { setCursor(Number(e.target.value)); setPlaying(false) }} />
      <span className={clsx('mono w-44 text-right', live ? 'text-emerald-300' : 'text-amber-300')} title={fmtDateTime(value)}>
        {live ? `now · ${fmtTime(now)}` : `${fmtTime(value)} · −${Math.round(now - value)}s`}
      </span>
      <span className="text-slate-600">{Math.round(span / 60)}m buffered</span>
    </div>
  )
}
