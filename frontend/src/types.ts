export type Evidence = 'exposed_reasoning' | 'tool_call' | 'execution' | 'inferred' | 'simulated'
export type Risk = 'info' | 'low' | 'medium' | 'high' | 'critical'

export interface NetMeta {
  host?: string; ip?: string; port?: number; scheme?: string; protocol?: string; scope?: string; encrypted?: boolean
  visibility?: string; direction?: string; state?: string; path?: string; resolved_ips?: string[]; ip_source?: string
  bytes_in?: number | null; bytes_out?: number | null
  geo?: { country?: string; country_code?: string; city?: string; lat?: number; lon?: number; isp?: string; provider?: string }
}

export interface AEvent {
  event_id: string; timestamp: string; agent_id: string; parent_agent_id: string | null
  event_type: string; action: string; target: string | null; tool: string | null
  status: 'pending' | 'running' | 'completed' | 'failed' | 'blocked' | 'info'
  duration_ms: number | null; metadata: Record<string, any> & { network?: NetMeta }; source: string
  instance_id?: string | null; agent_name?: string | null; category: string; evidence: Evidence; risk: Risk
  task_id?: string | null; parent_event_id?: string | null; input?: string | null; output?: string | null
  ts?: number
}

export interface Agent {
  agent_id: string; name: string; parent_agent_id: string | null; source: string; instance_id: string | null
  status: string; current_task: string | null; current_tool: string | null; model: string | null
  tokens_in: number; tokens_out: number; context_pct: number | null; first_seen: number; last_seen: number
  event_count: number; kind: string; metadata: Record<string, any>
}

export interface Alert {
  id: string; ts: number; last_ts: number; rule_id: string; severity: Risk; title: string; description: string
  agent_id: string; event_id: string; acked: boolean; count: number; data: Record<string, any>
}

export interface Metrics {
  container: string; ts: number; cpu_pct: number; mem_bytes: number; mem_limit: number; net_rx: number; net_tx: number
  rx_rate: number; tx_rate: number; pids: number; status: string; image: string; linked_instance: string | null
}

export interface AdapterStatus { state: string; message: string; stats: Record<string, any>; since: number | null; framework: string; name: string }

export interface Selection { kind: 'agent' | 'event' | 'resource' | 'edge' | 'process' | 'destination' | 'file' | 'alert' | 'container'; id: string; data?: any }

export type ViewId = 'overview' | 'map' | 'agents' | 'network' | 'filesystem' | 'processes' | 'commands' | 'timeline' | 'history' | 'alerts' | 'integrations' | 'settings'

export interface FieldSpec { name: string; label: string; type: 'text' | 'password' | 'number' | 'bool' | 'list'; default?: any; help?: string; required?: boolean }
export interface Framework { framework: string; display_name: string; description: string; mode: string; config_schema: FieldSpec[]; capabilities: Record<string, string> }
export interface Integration { id: string; framework: string; name: string; config: Record<string, any>; enabled: boolean; status: AdapterStatus }
