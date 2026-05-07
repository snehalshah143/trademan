import axios from 'axios'

const BASE = '/api/v1/monitored-positions'
const ALERTS_BASE = '/api/v1/monitor-alerts'

export interface MonitoredLeg {
  leg_id: string
  monitor_id: string
  leg_number: number
  instrument: string
  underlying: string
  strike: number | null
  option_type: string
  expiry: string
  side: 'BUY' | 'SELL'
  quantity: number
  lot_size: number
  entry_price: number
  current_price: number
  pnl: number
  premium_change_pct: number
}

export interface MonitoredPosition {
  monitor_id: string
  name: string
  strategy_type: string
  underlying: string
  exchange: string
  status: 'ACTIVE' | 'PAUSED' | 'CLOSED'
  notes: string | null
  created_at: string
  updated_at: string
  legs: MonitoredLeg[]
  alert_count: number
}

export interface MonitorIn {
  name: string
  strategy_type: string
  underlying: string
  exchange: string
  notes?: string
  legs: Array<{
    leg_number: number
    instrument: string
    underlying: string
    strike?: number | null
    option_type: string
    expiry: string
    side: string
    quantity: number
    lot_size: number
    entry_price: number
  }>
}

export interface MonitorMtm {
  monitor_id: string
  total_mtm: number
  total_mtm_pct: number
  legs: Array<{
    leg_id: string
    instrument: string
    side: string
    entry_price: number
    current_price: number
    pnl: number
    premium_change_pct: number
  }>
}

export interface MonitorAlertRule {
  alert_id: string
  position_id: string
  position_type: string
  strategy_name: string
  underlying: string
  name: string
  description: string | null
  is_active: boolean
  trigger_once: boolean
  cooldown_secs: number
  triggered_count: number
  last_triggered: string | null
  notify_popup: boolean
  notify_telegram: boolean
  notify_email: boolean
  notify_webhook: boolean
  notify_sound: boolean
  webhook_url: string | null
  telegram_chat_id: string | null
  condition_tree: object
  created_at: string
  updated_at: string
}

export interface MonitorAlertIn {
  position_id: string
  position_type?: string
  strategy_name?: string
  underlying?: string
  name: string
  description?: string
  is_active?: boolean
  trigger_once?: boolean
  cooldown_secs?: number
  notify_popup?: boolean
  notify_telegram?: boolean
  notify_email?: boolean
  notify_webhook?: boolean
  notify_sound?: boolean
  webhook_url?: string
  telegram_chat_id?: string
  condition_tree: object
}

export interface MonitorAlertStats {
  total_alerts: number
  active_alerts: number
  fired_today: number
  fired_this_week: number
  by_scope: Record<string, number>
}

export interface MonitorAlertHistoryItem {
  history_id: string
  alert_id: string
  position_id: string
  alert_name: string
  strategy_name: string
  underlying: string
  fired_at: string
  condition_summary: string
  context_snapshot: Record<string, unknown>
  notifications_sent: Record<string, unknown>
}

export interface MonitorAlertTemplate {
  template_id: string
  name: string
  description: string
  alert_count: number
  scopes: string[]
  preview: string[]
}

// ── Position API ───────────────────────────────────────────────────────────────

export const monitorService = {
  listAll: (params?: { status?: string; underlying?: string }) =>
    axios.get<MonitoredPosition[]>(BASE, { params }).then(r => r.data),

  get: (monitorId: string) =>
    axios.get<MonitoredPosition>(`${BASE}/${monitorId}`).then(r => r.data),

  create: (data: MonitorIn) =>
    axios.post<MonitoredPosition>(BASE, data).then(r => r.data),

  update: (monitorId: string, data: { name?: string; notes?: string }) =>
    axios.put<MonitoredPosition>(`${BASE}/${monitorId}`, data).then(r => r.data),

  updateLegPrice: (monitorId: string, legId: string, entryPrice: number) =>
    axios.patch<MonitoredLeg>(`${BASE}/${monitorId}/leg/${legId}`, { entry_price: entryPrice })
      .then(r => r.data),

  updateStatus: (monitorId: string, status: string) =>
    axios.patch<MonitoredPosition>(`${BASE}/${monitorId}/status`, { status }).then(r => r.data),

  delete: (monitorId: string) =>
    axios.delete(`${BASE}/${monitorId}`),

  getMtm: (monitorId: string) =>
    axios.get<MonitorMtm>(`${BASE}/${monitorId}/mtm`).then(r => r.data),
}

// ── Alert API ──────────────────────────────────────────────────────────────────

export const monitorAlertService = {
  list: (positionId?: string) =>
    axios.get<MonitorAlertRule[]>(ALERTS_BASE, { params: positionId ? { position_id: positionId } : {} })
      .then(r => r.data),

  get: (alertId: string) =>
    axios.get<MonitorAlertRule>(`${ALERTS_BASE}/${alertId}`).then(r => r.data),

  create: (data: MonitorAlertIn) =>
    axios.post<MonitorAlertRule>(ALERTS_BASE, data).then(r => r.data),

  update: (alertId: string, data: MonitorAlertIn) =>
    axios.put<MonitorAlertRule>(`${ALERTS_BASE}/${alertId}`, data).then(r => r.data),

  toggle: (alertId: string) =>
    axios.patch<MonitorAlertRule>(`${ALERTS_BASE}/${alertId}/toggle`).then(r => r.data),

  reset: (alertId: string) =>
    axios.patch<MonitorAlertRule>(`${ALERTS_BASE}/${alertId}/reset`).then(r => r.data),

  delete: (alertId: string) =>
    axios.delete(`${ALERTS_BASE}/${alertId}`),

  getStats: () =>
    axios.get<MonitorAlertStats>(`${ALERTS_BASE}/stats`).then(r => r.data),

  getHistory: (params?: { position_id?: string; alert_id?: string; limit?: number }) =>
    axios.get<MonitorAlertHistoryItem[]>(`${ALERTS_BASE}/history`, { params }).then(r => r.data),

  clearHistory: () =>
    axios.delete(`${ALERTS_BASE}/history`),

  getTemplates: () =>
    axios.get<MonitorAlertTemplate[]>(`${ALERTS_BASE}/templates`).then(r => r.data),

  createFromTemplate: (templateId: string, positionId: string, strategyName: string, underlying: string) =>
    axios.post(`${ALERTS_BASE}/from-template`, {
      template_id: templateId, position_id: positionId,
      strategy_name: strategyName, underlying,
    }).then(r => r.data),
}
