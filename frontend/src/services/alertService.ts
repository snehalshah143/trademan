import axios from 'axios'
import type { AlertRuleBuilderData } from '@/types/alertRules'

const BASE = '/api/v1/alert-rules'

export const alertRuleService = {
  list: (strategyId: string) =>
    axios.get<AlertRuleBuilderData[]>(BASE, { params: { strategy_id: strategyId } })
      .then(r => r.data),

  create: (data: Omit<AlertRuleBuilderData, 'alert_id' | 'triggered_count' | 'last_triggered' | 'created_at' | 'updated_at'>) =>
    axios.post<AlertRuleBuilderData>(BASE, data).then(r => r.data),

  update: (alertId: string, data: Omit<AlertRuleBuilderData, 'alert_id' | 'triggered_count' | 'last_triggered' | 'created_at' | 'updated_at'>) =>
    axios.put<AlertRuleBuilderData>(`${BASE}/${alertId}`, data).then(r => r.data),

  delete: (alertId: string) =>
    axios.delete(`${BASE}/${alertId}`),

  toggle: (alertId: string) =>
    axios.patch<AlertRuleBuilderData>(`${BASE}/${alertId}/toggle`).then(r => r.data),
}
