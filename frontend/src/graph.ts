import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from 'd3-force'
import type { AEvent, Agent, Evidence, Metrics } from './types'

export type NodeKind = 'agent' | 'resource' | 'container'
export interface GNode {
  id: string; kind: NodeKind; label: string; category: string; lastTs: number; count: number; running: boolean
  agent?: Agent; evidences: Evidence[]; eventIds: string[]; sub?: string; metrics?: Metrics
}
export interface GEdge {
  id: string; source: string; target: string; category: string; count: number; lastTs: number; running: boolean; failed: boolean
  reverse: boolean; structural: boolean; eventIds: string[]; evidences: Evidence[]
}
export interface Graph { nodes: GNode[]; edges: GEdge[]; sig: string }

const SKIP = new Set(['reasoning', 'task', 'error', 'system'])
const MAX_RESOURCES = 64

function dirOf(p: string): string {
  const clean = p.replace(/\\/g, '/')
  const parts = clean.split('/').filter(Boolean)
  const lead = clean.startsWith('/') ? '/' : ''
  if (parts.length <= 1) return lead || '.'
  return lead + parts.slice(0, Math.min(parts.length - 1, 3)).join('/')
}

function resourceFor(e: AEvent): { id: string; label: string; category: string; reverse: boolean } | null {
  const cat = e.category
  const net = e.metadata?.network
  const host = net?.host || net?.ip
  if (e.action === 'web_search') return { id: `web:search:${e.tool || ''}`, label: `Web search${e.tool ? ' · ' + e.tool : ''}`, category: 'web', reverse: false }
  switch (cat) {
    case 'web': return { id: `web:${host || e.target}`, label: host || e.target || 'web', category: 'web', reverse: false }
    case 'api': return { id: `api:${host || e.target}`, label: host || e.target || 'api', category: 'api', reverse: false }
    case 'network': return { id: `net:${host || e.target}`, label: host ? `${host}${net?.port ? ':' + net.port : ''}` : e.target || 'network', category: 'network', reverse: false }
    case 'terminal': return { id: `terminal:${e.agent_id}`, label: 'Terminal', category: 'terminal', reverse: false }
    case 'python': return { id: `python:${e.agent_id}`, label: e.tool === 'node' ? 'Node.js' : 'Code runtime', category: 'python', reverse: false }
    case 'file_read': return { id: `fs:${dirOf(e.target || '.')}`, label: dirOf(e.target || '.'), category: 'file_read', reverse: true }
    case 'file_write': return { id: `fs:${dirOf(e.target || '.')}`, label: dirOf(e.target || '.'), category: 'file_write', reverse: false }
    case 'file_delete': return { id: `fs:${dirOf(e.target || '.')}`, label: dirOf(e.target || '.'), category: 'file_delete', reverse: false }
    case 'database': return { id: `db:${e.target}`, label: (e.target || 'database').replace(/^.*\//, ''), category: 'database', reverse: false }
    case 'llm': return { id: `llm:${e.target || e.metadata?.model || 'llm'}`, label: String(e.target || e.metadata?.model || 'LLM'), category: 'llm', reverse: false }
    case 'memory': return { id: `memory:${e.instance_id || ''}`, label: 'Memory', category: 'memory', reverse: true }
    case 'subprocess': return { id: `proc:${e.metadata?.pid ?? e.target}`, label: `${e.tool || 'proc'}${e.metadata?.pid ? ' #' + e.metadata.pid : ''}`, category: 'subprocess', reverse: false }
    case 'tool': return { id: `tool:${e.tool || e.action}`, label: e.tool || e.action, category: 'tool', reverse: false }
    default: return null
  }
}

export function buildGraph(events: AEvent[], agents: Record<string, Agent>, metrics: Record<string, Metrics>, T: number, windowSec: number, live: boolean): Graph {
  const from = windowSec > 0 ? T - windowSec : 0
  const nodes = new Map<string, GNode>()
  const edges = new Map<string, GEdge>()
  const touch = (id: string, init: () => GNode, e: AEvent) => {
    let n = nodes.get(id)
    if (!n) { n = init(); nodes.set(id, n) }
    n.lastTs = Math.max(n.lastTs, e.ts!); n.count++
    if (e.status === 'running' || (e.duration_ms && e.ts! + e.duration_ms / 1000 > T)) n.running = true
    if (!n.evidences.includes(e.evidence)) n.evidences.push(e.evidence)
    n.eventIds.push(e.event_id)
    return n
  }
  const addEdge = (key: string, src: string, tgt: string, cat: string, reverse: boolean, e: AEvent, structural = false) => {
    let ed = edges.get(key)
    if (!ed) { ed = { id: key, source: src, target: tgt, category: cat, count: 0, lastTs: 0, running: false, failed: false, reverse, structural, eventIds: [], evidences: [] }; edges.set(key, ed) }
    ed.count++; ed.lastTs = Math.max(ed.lastTs, e.ts!)
    if (e.status === 'running' || (e.duration_ms && e.ts! + e.duration_ms / 1000 > T)) ed.running = true
    if (e.status === 'failed') ed.failed = true
    ed.eventIds.push(e.event_id)
    if (!ed.evidences.includes(e.evidence)) ed.evidences.push(e.evidence)
    ed.structural = false
  }
  const agentNode = (id: string, e: AEvent) => touch('a:' + id, () => ({
    id: 'a:' + id, kind: agents[id]?.kind === 'container' ? 'container' : 'agent', label: agents[id]?.name || e.agent_name || id, category: 'agent',
    lastTs: 0, count: 0, running: false, agent: agents[id], evidences: [], eventIds: [], metrics: undefined,
  }), e)

  const startIdx = (() => { let lo = 0, hi = events.length; while (lo < hi) { const m = (lo + hi) >> 1; if (events[m].ts! < from) lo = m + 1; else hi = m } return lo })()
  for (let i = startIdx; i < events.length; i++) {
    const e = events[i]
    if (e.metadata?.baseline || e.metadata?.ended) continue   // pre-existing processes / closed records are not new activity
    if (e.event_type === 'agent_lifecycle') { const n = agentNode(e.agent_id, e); n.count--; continue }
    if (SKIP.has(e.category) && e.category !== 'task') { if (e.category !== 'reasoning') agentNode(e.agent_id, e).count--; continue }
    if (e.category === 'task') { agentNode(e.agent_id, e).count--; continue }
    const an = agentNode(e.agent_id, e)
    if (an.kind === 'container') {
      const cname = e.metadata?.container as string | undefined
      if (cname) { an.metrics = metrics[cname]; an.label = cname }
    }
    if (e.category === 'delegation' || e.category === 'message') {
      const child = (e.metadata?.child_agent_id as string) || undefined
      const parentId = e.parent_agent_id
      if (e.category === 'delegation' && child) { agentNode(child, e); addEdge(`d:${e.agent_id}>${child}`, 'a:' + e.agent_id, 'a:' + child, 'delegation', false, e) }
      else if (parentId && e.category === 'message') { agentNode(parentId, e); addEdge(`m:${e.agent_id}>${parentId}`, 'a:' + e.agent_id, 'a:' + parentId, 'message', false, e) }
      else if (e.category === 'delegation') {
        const kids = Object.values(agents).filter((a) => a.parent_agent_id === e.agent_id)
        const hit = kids.find((k) => e.target && e.target.toLowerCase().startsWith(k.name.toLowerCase())) || kids[kids.length - 1]
        if (hit) { agentNode(hit.agent_id, e); addEdge(`d:${e.agent_id}>${hit.agent_id}`, 'a:' + e.agent_id, 'a:' + hit.agent_id, 'delegation', false, e) }
      }
      continue
    }
    const r = resourceFor(e)
    if (!r) continue
    const rn = touch('r:' + r.id, () => ({ id: 'r:' + r.id, kind: 'resource', label: r.label, category: r.category, lastTs: 0, count: 0, running: false, evidences: [], eventIds: [] }), e)
    rn.category = r.category === 'file_read' && rn.category !== 'file_read' ? rn.category : r.category
    addEdge(`${e.agent_id}>${r.id}:${r.category}`, 'a:' + e.agent_id, 'r:' + r.id, r.category, r.reverse, e)
  }

  // structural hierarchy edges (parent -> child), drawn dashed unless activity upgraded them
  for (const a of Object.values(agents)) {
    if (a.parent_agent_id && nodes.has('a:' + a.agent_id) && nodes.has('a:' + a.parent_agent_id)) {
      const hasActive = [...edges.values()].some((x) => x.source === 'a:' + a.parent_agent_id && x.target === 'a:' + a.agent_id)
      if (!hasActive) edges.set(`h:${a.parent_agent_id}>${a.agent_id}`, { id: `h:${a.parent_agent_id}>${a.agent_id}`, source: 'a:' + a.parent_agent_id, target: 'a:' + a.agent_id, category: 'delegation', count: 0, lastTs: 0, running: false, failed: false, reverse: false, structural: true, eventIds: [], evidences: [] })
    }
    if (a.kind === 'container' || !a.instance_id) continue
  }
  // link containers to the agents of the instance they host (structural, verified-vs-reported side by side)
  for (const n of nodes.values()) {
    if (n.kind !== 'container') continue
    const linked = n.metrics?.linked_instance || Object.values(agents).find((a) => a.agent_id === n.id.slice(2))?.metadata?.linked_instance
    for (const a of Object.values(agents)) {
      if (a.kind === 'agent' && a.instance_id && a.instance_id === linked && !a.parent_agent_id && nodes.has('a:' + a.agent_id))
        edges.set(`host:${n.id}>${a.agent_id}`, { id: `host:${n.id}>${a.agent_id}`, source: n.id, target: 'a:' + a.agent_id, category: 'system', count: 0, lastTs: 0, running: false, failed: false, reverse: false, structural: true, eventIds: [], evidences: [] })
    }
  }

  // cap resource nodes (most recently active win)
  const res = [...nodes.values()].filter((n) => n.kind === 'resource').sort((a, b) => b.lastTs - a.lastTs)
  for (const n of res.slice(MAX_RESOURCES)) nodes.delete(n.id)
  const ids = new Set(nodes.keys())
  const es = [...edges.values()].filter((e) => ids.has(e.source) && ids.has(e.target))
  const ns = [...nodes.values()]
  // drop agents that are only structural noise and idle for ages (keeps global view readable)
  const sig = ns.map((n) => n.id).sort().join('|') + '#' + es.map((e) => e.id).sort().join('|')
  void live
  return { nodes: ns, edges: es, sig }
}

export interface Pos { x: number; y: number; pinned?: boolean }

export function layout(g: Graph, pos: Map<string, Pos>) {
  const ids = new Set(g.nodes.map((n) => n.id))
  for (const k of [...pos.keys()]) if (!ids.has(k)) pos.delete(k)
  const hadAll = g.nodes.every((n) => pos.has(n.id))
  const adj = new Map<string, string[]>()
  g.edges.forEach((e) => { (adj.get(e.source) || adj.set(e.source, []).get(e.source)!).push(e.target); (adj.get(e.target) || adj.set(e.target, []).get(e.target)!).push(e.source) })
  const sn = g.nodes.map((n, i) => {
    let p = pos.get(n.id)
    if (!p) {
      const nb = (adj.get(n.id) || []).map((x) => pos.get(x)).find(Boolean)
      const ang = (i * 2.399963) % (Math.PI * 2)
      const base = nb || { x: 0, y: 0 }
      const r = nb ? 150 : 260
      p = { x: base.x + Math.cos(ang) * r, y: base.y + Math.sin(ang) * r }
    }
    return { id: n.id, kind: n.kind, x: p.x, y: p.y, fx: p.pinned ? p.x : undefined, fy: p.pinned ? p.y : undefined }
  })
  const links = g.edges.map((e) => ({ source: e.source, target: e.target, structural: e.structural, cat: e.category }))
  const sim = forceSimulation(sn as any)
    .force('link', forceLink(links as any).id((d: any) => d.id).distance((l: any) => (l.cat === 'delegation' || l.cat === 'message' ? 260 : l.cat === 'system' ? 210 : 165)).strength(0.55))
    .force('charge', forceManyBody().strength((d: any) => (d.kind === 'resource' ? -200 : -700)))
    .force('collide', forceCollide().radius((d: any) => (d.kind === 'resource' ? 70 : 86)))
    .force('x', forceX(0).strength(0.025)).force('y', forceY(0).strength(0.035))
    .alpha(hadAll ? 0.25 : 1).stop()
  const ticks = hadAll ? 60 : 320
  for (let i = 0; i < ticks; i++) sim.tick()
  sn.forEach((n: any) => { const old = pos.get(n.id); pos.set(n.id, { x: n.x, y: n.y, pinned: old?.pinned }) })
}
