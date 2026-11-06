import { memo, useEffect, useMemo, useRef, useState } from 'react'
import {
  BaseEdge, Background, Controls, EdgeLabelRenderer, Handle, MiniMap, Position, ReactFlow, ReactFlowProvider, getBezierPath, useReactFlow,
  type Edge, type EdgeProps, type Node, type NodeProps,
} from '@xyflow/react'
import { Box, Crosshair } from 'lucide-react'
import { useStore, visibleEvents, nowSec } from '../store'
import { buildGraph, layout, type GEdge, type GNode, type Pos } from '../graph'
import { CATS, Empty, agentColor, catOf, clsx, fmtBytes, shortAgent } from '../lib'
import { api } from '../api'

const ACTIVE_S = 5

const hidden = { opacity: 0, width: 1, height: 1, left: '50%', top: '50%', border: 0, background: 'transparent' } as const
const Handles = () => (<><Handle type="target" position={Position.Top} style={hidden} /><Handle type="source" position={Position.Top} style={hidden} /></>)

const AgentNode = memo(({ data, selected }: NodeProps<Node<{ n: GNode; T: number }>>) => {
  const { n } = data
  const a = n.agent
  const working = a?.status === 'working' || n.running
  const sub = !!a?.parent_agent_id
  const size = sub ? 54 : 70
  const color = agentColor(a?.agent_id || n.id)
  const title = a ? shortAgent(a.agent_id, a.name) : n.label
  const initials = (a?.name || n.label).replace(/^A\d+ \(.*?\) · /, '').slice(0, 2).toUpperCase() || 'AG'
  return (
    <div className="flex w-44 flex-col items-center" title={a?.name}>
      <Handles />
      <div className="relative" style={{ width: size, height: size }}>
        {working && <span className="pulse-ring absolute inset-0 rounded-full border-2" style={{ borderColor: color }} />}
        <div className={clsx('relative grid h-full w-full place-items-center rounded-full border-2 bg-panel font-bold shadow-lg', selected && 'ring-2 ring-white/70')}
          style={{ borderColor: color, color, boxShadow: working ? `0 0 22px ${color}55` : undefined, fontSize: sub ? 14 : 18 }}>
          {initials}
        </div>
        <span className={clsx('absolute -right-0.5 -top-0.5 h-3.5 w-3.5 rounded-full border-2 border-panel', working ? 'bg-cyan-400' : 'bg-slate-600')} />
      </div>
      <div className="mt-1.5 max-w-full truncate text-center text-[11px] font-semibold text-slate-200">{title}</div>
      <div className="max-w-full truncate text-center text-[10px] text-slate-500">
        {working ? (a?.current_tool ? `▸ ${a.current_tool}` : 'working') : 'idle'}{a?.model ? ` · ${a.model}` : ''}
      </div>
      {n.evidences.includes('simulated') && <span className="mt-0.5 rounded border border-dashed border-fuchsia-500/60 px-1 text-[8px] font-bold tracking-wider text-fuchsia-300">SIM</span>}
    </div>
  )
})

const ResourceNode = memo(({ data, selected }: NodeProps<Node<{ n: GNode; T: number }>>) => {
  const { n, T } = data
  const c = catOf(n.category)
  const age = Math.max(0, T - n.lastTs)
  const opacity = n.running ? 1 : Math.max(0.35, 1 - age / 900)
  const Icon = c.icon
  const dashed = n.evidences.every((e) => e === 'inferred' || e === 'simulated')
  return (
    <div style={{ opacity }} className={clsx('flex max-w-[190px] items-center gap-2 rounded-lg border bg-panel px-2.5 py-1.5 shadow-md', selected && 'ring-2 ring-white/70', dashed && 'border-dashed')}>
      <Handles />
      <Icon size={14} style={{ color: c.color }} className="shrink-0" />
      <div className="min-w-0"><div className="truncate text-[11px] font-medium text-slate-200" title={n.label}>{n.label}</div>
        <div className="text-[9px] text-slate-500">{c.label} · {n.count}×</div></div>
      {n.running && <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full" style={{ background: c.color }} />}
    </div>
  )
}, (a, b) => a.data.n === b.data.n && a.selected === b.selected && Math.floor(a.data.T / 5) === Math.floor(b.data.T / 5))

const ContainerNode = memo(({ data, selected }: NodeProps<Node<{ n: GNode; T: number }>>) => {
  const { n } = data
  const m = n.metrics
  return (
    <div className={clsx('w-44 rounded-lg border-2 border-emerald-500/50 bg-panel px-2.5 py-2 shadow-lg', selected && 'ring-2 ring-white/70')}>
      <Handles />
      <div className="flex items-center gap-2 text-[11px] font-semibold text-emerald-300"><Box size={14} /> <span className="truncate">{n.label}</span></div>
      <div className="mt-0.5 text-[9px] text-slate-500">Docker container · verified telemetry</div>
      {m && <div className="mt-1 flex justify-between text-[10px] text-slate-400"><span>CPU {m.cpu_pct.toFixed(0)}%</span><span>{fmtBytes(m.mem_bytes)}</span><span>↓{fmtBytes(m.rx_rate)}/s</span></div>}
    </div>
  )
})

const FlowEdge = memo((p: EdgeProps<Edge<{ e: GEdge; T: number; sourceLabel: string; targetLabel: string }>>) => {
  const { e, T } = p.data!
  const [path, lx, ly] = getBezierPath({ sourceX: p.sourceX, sourceY: p.sourceY, targetX: p.targetX, targetY: p.targetY, sourcePosition: p.sourcePosition, targetPosition: p.targetPosition, curvature: 0.18 })
  const color = e.failed ? '#ef4444' : catOf(e.category).color
  const active = e.running || T - e.lastTs < ACTIVE_S
  const recent = T - e.lastTs < 60
  const isDelete = e.category === 'file_delete'
  const isWrite = e.category === 'file_write'
  const width = e.structural ? 1.2 : Math.min(4, 1.3 + Math.log2(1 + e.count) * 0.5)
  const base = e.structural ? 0.25 : active ? 0.9 : recent ? 0.5 : 0.28
  const dur = isWrite || isDelete ? '1.1s' : '1.7s'
  const keyPoints = e.reverse ? '1;0' : '0;1'
  return (
    <>
      <path d={path} fill="none" stroke="transparent" strokeWidth={18} style={{ pointerEvents: 'stroke' }} />
      <BaseEdge id={p.id} path={path} style={{ stroke: color, strokeWidth: width, opacity: base, strokeDasharray: e.structural ? '3 6' : isDelete ? '2 4' : undefined, filter: active ? `drop-shadow(0 0 4px ${color})` : undefined }} />
      {active && !e.structural && <path d={path} fill="none" stroke={color} strokeWidth={width + 0.6} strokeDasharray={isWrite ? '10 6' : '4 8'} className={e.reverse ? 'edge-flow-rev' : 'edge-flow'} opacity={0.9} style={{ pointerEvents: 'none' }} />}
      {active && !e.structural && [0, 0.5].map((delay) => (
        <g key={delay} style={{ pointerEvents: 'none' }}>
          {isDelete ? <path d="M-4 -4L4 4M4 -4L-4 4" stroke={color} strokeWidth={2} strokeLinecap="round" />
            : isWrite ? <rect x={-4} y={-4} width={8} height={8} rx={1.5} fill={color} transform="rotate(45)" />
              : <circle r={3.2} fill={color} />}
          <animateMotion dur={dur} begin={`${delay * parseFloat(dur)}s`} repeatCount="indefinite" path={path} keyPoints={keyPoints} keyTimes="0;1" calcMode="linear" />
        </g>
      ))}
      {e.count > 1 && !e.structural && (
        <EdgeLabelRenderer><div style={{ transform: `translate(-50%,-50%) translate(${lx}px,${ly}px)`, pointerEvents: 'none', borderColor: color }}
          className="absolute rounded-full border bg-panel px-1.5 text-[9px] text-slate-300">{e.count}</div></EdgeLabelRenderer>
      )}
    </>
  )
})

const nodeTypes = { agent: AgentNode, container: ContainerNode, resource: ResourceNode }
const edgeTypes = { flow: FlowEdge }

const WINDOWS = [{ l: '2 min', v: 120 }, { l: '10 min', v: 600 }, { l: '30 min', v: 1800 }, { l: '2 hours', v: 7200 }, { l: 'All', v: 0 }]

function Inner() {
  const { events, agents, metrics, cursor, focusAgent, select, selection, setFocus, tick, setView } = useStore()
  void tick
  const [windowSec, setWindowSec] = useState(600)
  const [showLegend, setShowLegend] = useState(true)
  const pos = useRef<Map<string, Pos>>(new Map())
  const lastSig = useRef('')
  const fitted = useRef(false)
  const rf = useReactFlow()
  const T = cursor ?? nowSec()
  const vis = useMemo(() => visibleEvents({ events, cursor, focusAgent }), [events, cursor, focusAgent])
  const graph = useMemo(() => buildGraph(vis, agents, metrics, T, windowSec, cursor === null), [vis, agents, metrics, Math.floor(T), windowSec, cursor])
  const [, force] = useState(0)

  if (graph.sig !== lastSig.current) { layout(graph, pos.current); lastSig.current = graph.sig }
  useEffect(() => {
    if (graph.nodes.length && !fitted.current) {
      fitted.current = true
      const ts = [150, 700].map((ms) => setTimeout(() => rf.fitView({ padding: 0.2, duration: 350, maxZoom: 1.1 }), ms))
      return () => ts.forEach(clearTimeout)
    }
  }, [graph.sig])
  useEffect(() => { pos.current.clear(); lastSig.current = ''; fitted.current = false; force((x) => x + 1) }, [focusAgent])

  const labelOf = useMemo(() => Object.fromEntries(graph.nodes.map((n) => [n.id, n.label])), [graph])
  const nodes: Node[] = graph.nodes.map((n) => ({
    id: n.id, type: n.kind, position: { x: (pos.current.get(n.id)?.x ?? 0) - (n.kind === 'resource' ? 70 : 88), y: (pos.current.get(n.id)?.y ?? 0) - (n.kind === 'agent' ? 35 : 20) },
    data: { n, T }, selected: selection?.id === n.id,
  }))
  const edges: Edge[] = graph.edges.map((e) => ({ id: e.id, source: e.source, target: e.target, type: 'flow', data: { e, T, sourceLabel: labelOf[e.source], targetLabel: labelOf[e.target] }, selected: selection?.id === e.id }))

  if (!events.length) return <Empty><div><Crosshair className="mx-auto mb-3 opacity-40" size={30} />No activity recorded yet.<br />Add Agent Zero or start the labelled simulator from <button onClick={() => setView('integrations')} className="text-cyan-400 underline">Integrations</button>.</div></Empty>

  return (
    <div className="relative h-full grid-bg">
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} minZoom={0.15} maxZoom={2} proOptions={{ hideAttribution: true }}
        nodesConnectable={false} elementsSelectable
        onNodeDragStop={(_, node) => { pos.current.set(node.id, { x: node.position.x + (node.type === 'resource' ? 70 : 88), y: node.position.y + (node.type === 'agent' ? 35 : 20), pinned: true }) }}
        onNodeClick={(_, node) => {
          const n = (node.data as any).n as GNode
          if (n.kind === 'resource') select({ kind: 'resource', id: n.id, data: { label: n.label, category: n.category, eventIdList: n.eventIds } })
          else if (n.kind === 'container') select({ kind: 'container', id: n.label })
          else select({ kind: 'agent', id: n.agent?.agent_id || n.id.slice(2) })
        }}
        onEdgeClick={(_, edge) => {
          const d = (edge.data as any); const e = d.e as GEdge
          if (!e.structural) select({ kind: 'edge', id: e.id, data: { category: e.category, sourceLabel: d.sourceLabel, targetLabel: d.targetLabel, direction: e.reverse ? 'inbound (data read into the agent)' : 'outbound', eventIdList: e.eventIds } })
        }}
        onPaneClick={() => select(null)}>
        <Background gap={28} color="#14203a" />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor={(n) => ((n.data as any).n.kind === 'resource' ? catOf((n.data as any).n.category).color : agentColor(((n.data as any).n.agent?.agent_id) || n.id))} maskColor="#060a13aa" />
      </ReactFlow>

      <div className="absolute left-3 top-3 flex items-center gap-2 rounded-lg border border-line bg-panel/90 px-2 py-1.5 text-xs backdrop-blur">
        <span className="text-slate-500">Show last</span>
        <select value={windowSec} onChange={(e) => setWindowSec(Number(e.target.value))} className="rounded border border-line bg-ink px-1.5 py-0.5">
          {WINDOWS.map((w) => <option key={w.v} value={w.v}>{w.l}</option>)}</select>
        <span className="text-slate-600">·</span><span className="text-slate-400">{graph.nodes.length} nodes · {graph.edges.filter((e) => !e.structural).length} links</span>
        {focusAgent && <button className="rounded border border-cyan-500/50 px-1.5 text-cyan-200" onClick={() => setFocus(null)}>focused · clear</button>}
        <button className="text-slate-500 hover:text-slate-200" onClick={() => rf.fitView({ padding: 0.25, duration: 400 })}>fit</button>
        <button className="text-slate-500 hover:text-slate-200" onClick={() => { pos.current.clear(); lastSig.current = ''; force((x) => x + 1) }}>relayout</button>
        <button className="text-slate-500 hover:text-slate-200" onClick={() => setShowLegend(!showLegend)}>legend</button>
      </div>
      {showLegend && (
        <div className="absolute bottom-3 left-3 w-60 rounded-lg border border-line bg-panel/90 p-2.5 text-[10px] backdrop-blur">
          <div className="mb-1.5 font-semibold uppercase tracking-wider text-slate-500">Operations</div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1">
            {['web', 'api', 'terminal', 'python', 'file_read', 'file_write', 'file_delete', 'subprocess', 'delegation', 'database', 'llm', 'memory'].map((k) => (
              <div key={k} className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: CATS[k].color }} />{CATS[k].label}</div>))}
          </div>
          <div className="mt-2 border-t border-line pt-1.5 text-slate-500 leading-snug">● dot = read/call · ◆ diamond = write · ✕ = delete.<br />Particles flow in the direction of data.<br />Dashed node border = inferred/simulated only. Dashed link = hierarchy.</div>
        </div>
      )}
    </div>
  )
}

export default function LiveMap() {
  void api
  return <ReactFlowProvider><Inner /></ReactFlowProvider>
}
