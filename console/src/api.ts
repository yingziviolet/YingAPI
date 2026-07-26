import type {
  AlertItem,
  BreakerState,
  CacheStats,
  Channel,
  ChannelBalance,
  ChannelStat,
  DailyStat,
  KeySpend,
  ModelStat,
  Overview,
  RequestLogItem,
  SubscriptionUsage,
  VirtualKey,
} from './types'

const TOKEN_KEY = 'gw_admin_token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? ''
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`,
      ...(init?.headers ?? {}),
    },
  })
  if (resp.status === 401) throw new ApiError(401, '管理 token 无效')
  if (!resp.ok) {
    let message = `HTTP ${resp.status}`
    try {
      const data = await resp.json()
      message = data.detail ?? data.error?.message ?? message
      if (typeof message !== 'string') message = JSON.stringify(message)
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, message)
  }
  if (resp.status === 204) return undefined as T
  return resp.json()
}

export const api = {
  channels: () => request<Channel[]>('/admin/channels'),
  createChannel: (body: object) =>
    request<Channel>('/admin/channels', { method: 'POST', body: JSON.stringify(body) }),
  updateChannel: (id: number, body: object) =>
    request<Channel>(`/admin/channels/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteChannel: (id: number) => request<void>(`/admin/channels/${id}`, { method: 'DELETE' }),
  testChannel: (id: number) =>
    request<{ ok: boolean; status_code: number | null; latency_ms: number; error?: string }>(
      `/admin/channels/${id}/test`,
      { method: 'POST' },
    ),

  keys: () => request<VirtualKey[]>('/admin/keys'),
  createKey: (body: object) =>
    request<VirtualKey & { key: string }>('/admin/keys', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateKey: (id: number, body: object) =>
    request<VirtualKey>(`/admin/keys/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  rotateKey: (id: number) =>
    request<VirtualKey & { key: string }>(`/admin/keys/${id}/rotate`, { method: 'POST' }),
  deleteKey: (id: number) => request<void>(`/admin/keys/${id}`, { method: 'DELETE' }),
  keySpend: (id: number) => request<KeySpend>(`/admin/keys/${id}/spend`),

  overview: (days = 7) => request<Overview>(`/admin/stats/overview?days=${days}`),
  statsChannels: (days = 7) => request<ChannelStat[]>(`/admin/stats/channels?days=${days}`),
  statsModels: (days = 7) => request<ModelStat[]>(`/admin/stats/models?days=${days}`),
  statsDaily: (days = 7) => request<DailyStat[]>(`/admin/stats/daily?days=${days}`),
  statsCache: () => request<CacheStats>('/admin/stats/cache'),
  breakers: () => request<BreakerState[]>('/admin/breakers'),
  resetBreaker: (channelId: number) =>
    request<void>(`/admin/breakers/${channelId}/reset`, { method: 'POST' }),
  logs: (limit = 100) => request<RequestLogItem[]>(`/admin/logs?limit=${limit}`),

  alerts: (includeAcked = false) =>
    request<AlertItem[]>(`/admin/alerts?include_acked=${includeAcked}`),
  ackAlert: (id: number) => request<AlertItem>(`/admin/alerts/${id}/ack`, { method: 'POST' }),
  ackAllAlerts: () => request<{ acknowledged: number }>('/admin/alerts/ack-all', { method: 'POST' }),
  runSentinel: () => request<{ ok: boolean }>('/admin/sentinel/run', { method: 'POST' }),
  subscriptionUsage: (days = 7) =>
    request<SubscriptionUsage>(`/admin/subscription-usage?days=${days}`),

  balances: () => request<ChannelBalance[]>('/admin/balances'),
  channelBalance: (id: number) => request<ChannelBalance>(`/admin/channels/${id}/balance`),
}

export function liveTailUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/admin/ws/livetail?token=${encodeURIComponent(getToken())}`
}

export function fmtUsd(v: number | null | undefined): string {
  if (v == null) return '—'
  if (v === 0) return '$0'
  if (v < 0.01) return `$${v.toFixed(6)}`
  return `$${v.toFixed(2)}`
}

export function fmtTokens(v: number | null | undefined): string {
  if (v == null) return '—'
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`
  return String(v)
}
