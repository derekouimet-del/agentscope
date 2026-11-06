const TOKEN_KEY = 'agentscope.token'

export const getToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const setToken = (t: string) => (t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY))

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const r = await fetch(path, {
    method: opts.method || (opts.body ? 'POST' : 'GET'),
    headers: { Authorization: `Bearer ${getToken()}`, ...(opts.body ? { 'Content-Type': 'application/json' } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  })
  if (r.status === 401) { setToken(''); window.dispatchEvent(new Event('agentscope:logout')) }
  if (!r.ok) {
    let msg = r.statusText
    try { const j = await r.json(); msg = j.detail || j.message || msg } catch { /* ignore */ }
    throw new ApiError(r.status, typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return r.json()
}

export async function download(path: string, filename: string) {
  const r = await fetch(path, { headers: { Authorization: `Bearer ${getToken()}` } })
  if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => ({}))).detail || r.statusText)
  const url = URL.createObjectURL(await r.blob())
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

export function qs(params: Record<string, string | number | undefined | null | boolean>) {
  const u = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '' && v !== false) u.set(k, String(v)) })
  const s = u.toString()
  return s ? `?${s}` : ''
}
