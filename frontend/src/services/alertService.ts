import axios from 'axios'
import type { AlertRuleBuilderData } from '@/types/alertRules'

const BASE = '/api/v1/alert-rules'

export interface AlertStats {
  total_alerts: number
  active_alerts: number
  fired_today: number
  fired_this_week: number
  most_triggered: Array<{ alert_id: string; name: string; count: number }>
  by_scope: Record<string, number>
}

export interface AlertHistoryItem {
  id: string
  strategy_id: string | null
  rule_id: string | null
  symbol: string | null
  message: string
  severity: string
  triggered_at: string
  dismissed: boolean
}

export interface AlertTemplate {
  template_id: string
  name: string
  description: string
  alert_count: number
  scopes: string[]
  preview: string[]
}

export const alertRuleService = {
  list: (strategyId: string) =>
    axios.get<AlertRuleBuilderData[]>(BASE, { params: { strategy_id: strategyId } })
      .then(r => r.data),

  listAll: (isActive?: boolean) =>
    axios.get<AlertRuleBuilderData[]>(BASE, { params: isActive !== undefined ? { is_active: isActive } : {} })
      .then(r => r.data),

  create: (data: Omit<AlertRuleBuilderData, 'alert_id' | 'triggered_count' | 'last_triggered' | 'created_at' | 'updated_at'>) =>
    axios.post<AlertRuleBuilderData>(BASE, data).then(r => r.data),

  update: (alertId: string, data: Omit<AlertRuleBuilderData, 'alert_id' | 'triggered_count' | 'last_triggered' | 'created_at' | 'updated_at'>) =>
    axios.put<AlertRuleBuilderData>(`${BASE}/${alertId}`, data).then(r => r.data),

  delete: (alertId: string) =>
    axios.delete(`${BASE}/${alertId}`),

  toggle: (alertId: string) =>
    axios.patch<AlertRuleBuilderData>(`${BASE}/${alertId}/toggle`).then(r => r.data),

  reset: (alertId: string) =>
    axios.patch<AlertRuleBuilderData>(`${BASE}/${alertId}/reset`).then(r => r.data),

  getStats: () =>
    axios.get<AlertStats>(`${BASE}/stats`).then(r => r.data),

  getHistory: (params?: { strategy_id?: string; rule_id?: string; limit?: number }) =>
    axios.get<AlertHistoryItem[]>(`${BASE}/history`, { params }).then(r => r.data),

  clearHistory: () =>
    axios.delete(`${BASE}/history`),

  getTemplates: () =>
    axios.get<AlertTemplate[]>(`${BASE}/templates`).then(r => r.data),

  createFromTemplate: (templateId: string, strategyId: string, customizations?: Record<string, unknown>) =>
    axios.post(`${BASE}/from-template`, { template_id: templateId, strategy_id: strategyId, customizations: customizations ?? {} })
      .then(r => r.data),
}
