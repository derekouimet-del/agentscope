import { create } from 'zustand'
import { api, getToken, setToken } from './api'
import type { AEvent, AdapterStatus, Agent, Alert, Metrics, Selection, ViewId } from './types'

const MAX_EVENTS = 6000

const withTs = (e: AEvent): AEvent => ({ ...e, ts: Date.parse(e.timestamp) / 1000 })

interface State {
  user: { username: string; role: string } | null
  view: ViewId
  events: AEvent[]            // ascending by time
  index: Map<string, number>
  agents: Record<string, Agent>
  alerts: Alert[]
  metrics: Record<string, Metrics>
  metricHistory: Record<string, { ts: number; rx: number; tx: number; cpu: number }[]>
  adapters: Record<string, AdapterStatus>
  overview: any
  wsState: 'connecting' | 'open' | 'closed'
  selection: Selection | null
  focusAgent: string | null
  cursor: number | null        // null = live; otherwise replay time (unix seconds)
  playing: boolean
  speed: number
  serverSkew: number
  tick: number
  setView: (v: ViewId) => void
  select: (s: Selection | null) => void
  setFocus: (id: string | null) => void
  setCursor: (t: number | null) => void
  setPlaying: (p: boolean) => void
  setSpeed: (s: number) => void
  login: (token: string, user: { username: string; role: string }) => void
  logout: () => void
  bootstrap: () => Promise<void>
  refreshOverview: () => Promise<void>
  refreshAlerts: () => Promise<void>
  upsert: (e: AEvent) => void
  connect: () => void
}

let ws: WebSocket | null = null
let retry = 0
let buffer: AEvent[] = []
let flushTimer: number | null = null

export const useStore = create<State>((set, get) => ({
  user: null, view: 'overview', events: [], index: new Map(), agents: {}, alerts: [], metrics: {}, metricHistory: {}, adapters: {},
  overview: null, wsState: 'closed', selection: null, focusAgent: null, cursor: null, playing: false, speed: 1, serverSkew: 0, tick: 0,

  setView: (view) => set({ view }),
  select: (selection) => set({ selection }),
  setFocus: (focusAgent) => set({ focusAgent }),
  setCursor: (cursor) => set({ cursor, playing: cursor === null ? false : get().playing }),
  setPlaying: (playing) => set({ playing }),
  setSpeed: (speed) => set({ speed }),

  login: (token, user) => { setToken(token); set({ user }); get().bootstrap(); get().connect() },
  logout: () => { setToken(''); ws?.close(); ws = null; set({ user: null, events: [], index: new Map(), agents: {}, alerts: [] }) },

  bootstrap: async () => {
    const since = Date.now() / 1000 - 6 * 3600
    const [ev, agents, alerts] = await Promise.all([
      api<{ events: AEvent[] }>(`/api/events?limit=3000&since=${since}`),
      api<Agent[]>('/api/agents'),
      api<Alert[]>('/api/alerts'),
    ])
    const sorted = ev.events.map(withTs).sort((a, b) => a.ts! - b.ts!)
    const index = new Map<string, number>()
    sorted.forEach((e, i) => index.set(e.event_id, i))
    set({ events: sorted, index, agents: Object.fromEntries(agents.map((a) => [a.agent_id, a])), alerts })
    get().refreshOverview()
  },

  refreshOverview: async () => { try { set({ overview: await api('/api/overview') }) } catch { /* ignore */ } },
  refreshAlerts: async () => { try { set({ alerts: await api<Alert[]>('/api/alerts') }) } catch { /* ignore */ } },

  upsert: (e) => {
    buffer.push(withTs(e))
    if (flushTimer === null) {
      flushTimer = window.setTimeout(() => {   // batch bursts into one render
        flushTimer = null
        const batch = buffer; buffer = []
        set((s) => {
          const events = s.events.slice()
          const index = new Map(s.index)
          let needSort = false
          for (const ev of batch) {
            const i = index.get(ev.event_id)
            if (i !== undefined && events[i]?.event_id === ev.event_id) events[i] = ev
            else {
              if (events.length && ev.ts! < events[events.length - 1].ts!) needSort = true
              index.set(ev.event_id, events.length); events.push(ev)
            }
          }
          if (needSort) events.sort((a, b) => a.ts! - b.ts!)
          let out = events
          if (events.length > MAX_EVENTS) out = events.slice(events.length - MAX_EVENTS)
          if (needSort || out !== events) { index.clear(); out.forEach((x, i) => index.set(x.event_id, i)) }
          const sel = s.selection
          return { events: out, index, selection: sel }
        })
      }, 120)
    }
  },

  connect: () => {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    set({ wsState: 'connecting' })
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(getToken())}`)
    ws.onopen = () => { retry = 0; set({ wsState: 'open' }) }
    ws.onclose = () => {
      set({ wsState: 'closed' })
      if (!get().user) return
      retry = Math.min(retry + 1, 6)
      setTimeout(() => { if (get().user) { get().connect(); get().bootstrap().catch(() => {}) } }, 1000 * 2 ** retry / 2)
    }
    ws.onmessage = (m) => {
      const msg = JSON.parse(m.data)
      switch (msg.type) {
        case 'hello':
          set({ agents: Object.fromEntries((msg.agents as Agent[]).map((a) => [a.agent_id, a])), adapters: msg.adapters,
                metrics: msg.metrics, serverSkew: msg.server_time - Date.now() / 1000 })
          break
        case 'event': case 'event_update': get().upsert(msg.event); break
        case 'agent': set((s) => ({ agents: { ...s.agents, [msg.agent.agent_id]: msg.agent } })); break
        case 'alert':
          set((s) => {
            const i = s.alerts.findIndex((a) => a.id === msg.alert.id)
            const alerts = s.alerts.slice()
            if (i >= 0) alerts[i] = msg.alert; else alerts.unshift(msg.alert)
            return { alerts }
          }); break
        case 'alert_ack': get().refreshAlerts(); break
        case 'adapter_status': set((s) => ({ adapters: { ...s.adapters, [msg.id]: msg.status } })); break
        case 'metrics':
          set((s) => {
            const h = (s.metricHistory[msg.metrics.container] || []).concat({ ts: msg.metrics.ts, rx: msg.metrics.rx_rate, tx: msg.metrics.tx_rate, cpu: msg.metrics.cpu_pct }).slice(-90)
            return { metrics: { ...s.metrics, [msg.metrics.container]: msg.metrics }, metricHistory: { ...s.metricHistory, [msg.metrics.container]: h } }
          }); break
        case 'ping': set({ overview: { ...get().overview, ...msg.overview } }); break
        case 'resync': get().bootstrap(); break
      }
    }
  },
}))

// replay clock: advances cursor while playing
let last = performance.now()
setInterval(() => {
  const now = performance.now(); const dt = (now - last) / 1000; last = now
  const s = useStore.getState()
  s.tick !== undefined && useStore.setState({ tick: s.tick + 1 })
  if (s.playing && s.cursor !== null) {
    const next = s.cursor + dt * s.speed
    if (next >= Date.now() / 1000 - 0.5) useStore.setState({ cursor: null, playing: false })
    else useStore.setState({ cursor: next })
  }
}, 250)

export const nowSec = () => Date.now() / 1000

/** Events visible at the current time cursor, honouring agent focus. */
export function visibleEvents(s: Pick<State, 'events' | 'cursor' | 'focusAgent'>): AEvent[] {
  let ev = s.events
  if (s.cursor !== null) {
    let hi = ev.length
    let lo = 0
    while (lo < hi) { const mid = (lo + hi) >> 1; if (ev[mid].ts! <= s.cursor) lo = mid + 1; else hi = mid }
    ev = ev.slice(0, lo)
  }
  if (s.focusAgent) ev = ev.filter((e) => e.agent_id === s.focusAgent || e.parent_agent_id === s.focusAgent)
  return ev
}
