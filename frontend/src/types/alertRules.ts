// ─── Types for the Alert Rule Builder (nested condition tree system) ──────────
// Separate from the existing AlertRule/AlertEvent types in domain.ts

export type ConditionScope = 'STRATEGY' | 'LEG' | 'SPOT' | 'INDICATOR'

export type ConditionOperator =
  | 'GTE' | 'LTE' | 'GT' | 'LT' | 'EQ'
  | 'CROSS_ABOVE' | 'CROSS_BELOW'

export interface Condition {
  id: string
  scope: ConditionScope
  metric: string
  operator: ConditionOperator
  value: number | null
  leg_id: string | null
  params: Record<string, unknown>
}

export interface ConditionGroup {
  id: string
  op: 'AND' | 'OR'
  conditions: Condition[]
  groups: ConditionGroup[]
}

export interface AlertRuleBuilderData {
  alert_id?: string
  strategy_id: string
  name: string
  description: string
  is_active: boolean
  trigger_once: boolean
  cooldown_secs: number
  triggered_count?: number
  last_triggered?: string | null
  notify_popup: boolean
  notify_telegram: boolean
  notify_email: boolean
  notify_webhook: boolean
  notify_sound: boolean
  webhook_url: string
  telegram_chat_id: string
  condition_tree: ConditionGroup
  created_at?: string
  updated_at?: string
}

// ── Metric options per scope ──────────────────────────────────────────────────

export const METRIC_OPTIONS: Record<ConditionScope, Array<{ value: string; label: string }>> = {
  STRATEGY: [
    { value: 'MTM',     label: 'MTM (P&L)' },
    { value: 'PNL_PCT', label: 'PnL %' },
    { value: 'PROFIT',  label: 'Profit' },
    { value: 'LOSS',    label: 'Loss' },
  ],
  LEG: [
    { value: 'LTP',             label: 'LTP' },
    { value: 'PREMIUM_CHANGE',  label: 'Premium change %' },
    { value: 'PNL',             label: 'Leg P&L' },
  ],
  SPOT: [
    { value: 'SPOT_PRICE',        label: 'Spot price' },
    { value: 'SPOT_VS_SUPERTREND', label: 'Spot vs Supertrend' },
  ],
  INDICATOR: [
    { value: 'RSI',       label: 'RSI' },
    { value: 'SUPERTREND', label: 'Supertrend' },
    { value: 'EMA_CROSS', label: 'EMA Cross' },
  ],
}

export const OPERATOR_OPTIONS: Array<{ value: ConditionOperator; label: string }> = [
  { value: 'GTE',         label: '>=' },
  { value: 'LTE',         label: '<=' },
  { value: 'GT',          label: '>'  },
  { value: 'LT',          label: '<'  },
  { value: 'EQ',          label: '='  },
  { value: 'CROSS_ABOVE', label: 'Cross above' },
  { value: 'CROSS_BELOW', label: 'Cross below' },
]

export const CROSS_OPERATORS = new Set<ConditionOperator>(['CROSS_ABOVE', 'CROSS_BELOW'])

export function defaultCondition(scope: ConditionScope = 'STRATEGY'): Condition {
  return {
    id:       crypto.randomUUID(),
    scope,
    metric:   METRIC_OPTIONS[scope][0].value,
    operator: 'LTE',
    value:    0,
    leg_id:   null,
    params:   {},
  }
}

export function defaultGroup(): ConditionGroup {
  return {
    id:         crypto.randomUUID(),
    op:         'AND',
    conditions: [defaultCondition('STRATEGY')],
    groups:     [],
  }
}

export function defaultAlertRule(strategyId: string): AlertRuleBuilderData {
  return {
    strategy_id:      strategyId,
    name:             '',
    description:      '',
    is_active:        true,
    trigger_once:     false,
    cooldown_secs:    60,
    notify_popup:     true,
    notify_telegram:  false,
    notify_email:     false,
    notify_webhook:   false,
    notify_sound:     false,
    webhook_url:      '',
    telegram_chat_id: '',
    condition_tree:   defaultGroup(),
  }
}

// ── Preview text builder ──────────────────────────────────────────────────────

function conditionText(c: Condition): string {
  const metricLabel = METRIC_OPTIONS[c.scope]?.find(m => m.value === c.metric)?.label ?? c.metric
  const opLabel = OPERATOR_OPTIONS.find(o => o.value === c.operator)?.label ?? c.operator
  const legPart = c.leg_id ? ` [Leg ${c.leg_id}]` : ''
  const valPart = CROSS_OPERATORS.has(c.operator as ConditionOperator)
    ? ''
    : ` ${c.value ?? 0}`
  return `${c.scope}${legPart} ${metricLabel} ${opLabel}${valPart}`
}

function groupText(g: ConditionGroup, depth = 0): string {
  const indent = '  '.repeat(depth)
  const parts: string[] = [
    ...g.conditions.map(c => `${indent}${conditionText(c)}`),
    ...g.groups.map(sg => `${indent}[${sg.op}]\n${groupText(sg, depth + 1)}`),
  ]
  return parts.join(`\n${indent}${g.op} `)
}

export function buildPreviewText(tree: ConditionGroup): string {
  return groupText(tree, 0) || '(no conditions)'
}
