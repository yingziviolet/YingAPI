export interface Channel {
  id: number
  name: string
  provider: string
  base_url: string
  models: string[]
  model_map: Record<string, string>
  prices: Record<string, { input?: number; output?: number }>
  priority: number
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface VirtualKey {
  id: number
  name: string
  key_masked: string
  enabled: boolean
  monthly_budget_usd: number | null
  rpm_limit: number | null
  created_at: string
}

export interface Overview {
  window_days: number
  requests: number
  prompt_tokens: number
  completion_tokens: number
  cost_usd: number
  cache_hits: number
  cache_hit_rate: number
  errors: number
  error_rate: number
}

export interface ChannelStat {
  channel_id: number | null
  channel_name: string
  requests: number
  total_tokens: number
  cost_usd: number
  avg_latency_ms: number
}

export interface ModelStat {
  model: string
  requests: number
  total_tokens: number
  cost_usd: number
}

export interface DailyStat {
  date: string
  requests: number
  total_tokens: number
  cost_usd: number
  cache_hits: number
}

export interface BreakerState {
  channel_id: number
  channel_name: string
  state: 'closed' | 'open' | 'half_open'
  window_requests: number
  window_failures: number
  error_rate: number
  opened_count: number
  cooldown_remaining_s: number
}

export interface CacheStats {
  exact: { entries: number; total_hits: number }
  semantic: { entries: number; total_hits: number }
}

export interface KeySpend {
  key_id: number
  name: string
  month_to_date_usd: number
  monthly_budget_usd: number | null
}

export interface AlertItem {
  id: number
  kind: string
  severity: 'info' | 'warning' | 'critical'
  title: string
  detail: string
  acknowledged: boolean
  created_at: string
}

export interface SubscriptionUsage {
  available: boolean
  reason?: string
  window_days?: number
  files_scanned?: number
  totals?: {
    messages: number
    input_tokens: number
    output_tokens: number
    cache_read_tokens: number
    cache_creation_tokens: number
  }
  est_api_cost_usd?: number
  daily?: {
    date: string
    messages: number
    input_tokens: number
    output_tokens: number
    cache_read_tokens: number
    cache_creation_tokens: number
    est_cost_usd: number
  }[]
  by_model?: {
    model: string
    messages: number
    input_tokens: number
    output_tokens: number
    cache_read_tokens: number
    cache_creation_tokens: number
    est_cost_usd: number
  }[]
}

export interface RequestLogItem {
  id?: number
  ts?: number
  trace_id: string
  virtual_key_id: number | null
  channel_id: number | null
  model: string
  upstream_model: string | null
  stream: boolean
  cache_hit: boolean
  status: string
  status_code: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  usage_source?: string
  cost_usd: number | null
  latency_ms: number | null
  first_token_ms: number | null
  error: string | null
  created_at?: string
}
