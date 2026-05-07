// ─── Types for the Alert Rule Builder (Chartink-style condition tree) ────────

export type ConditionScope = 'STRATEGY' | 'LEG' | 'SPOT' | 'INDICATOR' | 'CANDLE'

export type Timeframe = '1m' | '3m' | '5m' | '15m' | '75m' | '1d' | '1w' | '1M'

export type ConditionOperator =
  | 'GTE' | 'LTE' | 'GT' | 'LT' | 'EQ'
  | 'CROSS_ABOVE' | 'CROSS_BELOW'

// ── Param & Metric config ─────────────────────────────────────────────────────

export interface ParamDef {
  key:     string
  label:   string
  default: number
  min?:    number
  max?:    number
  step?:   number
}

export interface MetricConfig {
  value:           string
  label:           string
  unit:            '₹' | '%' | '' | 'pts'
  needsTimeframe:  boolean
  params:          ParamDef[]
  description?:    string
}

export const METRIC_CONFIGS: Record<ConditionScope, MetricConfig[]> = {
  STRATEGY: [
    {
      value: 'MTM',        label: 'MTM P&L (₹)',         unit: '₹', needsTimeframe: false, params: [],
      description: 'Net strategy profit/loss in ₹',
    },
    {
      value: 'MTM_PCT',    label: 'MTM %',                unit: '%', needsTimeframe: false, params: [],
      description: 'Strategy P&L as % of initial capital',
    },
    {
      value: 'MAX_PROFIT', label: 'Max Profit Hit (₹)',   unit: '₹', needsTimeframe: false, params: [],
      description: 'Highest MTM reached in session',
    },
    {
      value: 'MAX_LOSS',   label: 'Max Loss Hit (₹)',     unit: '₹', needsTimeframe: false, params: [],
      description: 'Deepest drawdown in session (absolute ₹)',
    },
  ],
  LEG: [
    { value: 'LTP',             label: 'LTP (₹)',             unit: '₹', needsTimeframe: false, params: [] },
    {
      value: 'PREMIUM_CHG_PCT', label: 'Premium Change %',    unit: '%', needsTimeframe: false, params: [],
      description: 'LTP change % from entry price',
    },
    {
      value: 'PREMIUM_CHG_ABS', label: 'Premium Change (₹)', unit: '₹', needsTimeframe: false, params: [],
      description: 'LTP change ₹ from entry price',
    },
    { value: 'LEG_PNL',         label: 'Leg P&L (₹)',         unit: '₹', needsTimeframe: false, params: [] },
  ],
  SPOT: [
    { value: 'SPOT_PRICE',   label: 'Spot Price (₹)',    unit: '₹', needsTimeframe: false, params: [] },
    {
      value: 'SPOT_CHG_PCT', label: 'Spot Change %',     unit: '%', needsTimeframe: false, params: [],
      description: 'Intraday price change %',
    },
    {
      value: 'SPOT_VS_VWAP', label: 'Spot vs VWAP',      unit: '₹', needsTimeframe: false, params: [],
      description: 'Spot price relative to VWAP',
    },
  ],
  INDICATOR: [
    {
      value: 'EMA', label: 'EMA', unit: '₹', needsTimeframe: true,
      params: [{ key: 'period', label: 'Period', default: 21, min: 2, max: 500 }],
      description: 'Exponential Moving Average',
    },
    {
      value: 'SMA', label: 'SMA', unit: '₹', needsTimeframe: true,
      params: [{ key: 'period', label: 'Period', default: 20, min: 2, max: 500 }],
      description: 'Simple Moving Average',
    },
    {
      value: 'SUPERTREND', label: 'Supertrend', unit: '', needsTimeframe: true,
      params: [
        { key: 'period', label: 'Period', default: 10, min: 2 },
        { key: 'factor', label: 'Factor', default: 3,  min: 0.1, step: 0.1 },
      ],
      description: 'Supertrend trend indicator',
    },
    {
      value: 'RSI', label: 'RSI', unit: '', needsTimeframe: true,
      params: [{ key: 'period', label: 'Period', default: 14, min: 2, max: 100 }],
      description: 'Relative Strength Index (0–100)',
    },
    {
      value: 'MACD_HIST', label: 'MACD Histogram', unit: '', needsTimeframe: true,
      params: [
        { key: 'fast',   label: 'Fast',   default: 12, min: 1 },
        { key: 'slow',   label: 'Slow',   default: 26, min: 1 },
        { key: 'signal', label: 'Signal', default: 9,  min: 1 },
      ],
      description: 'MACD Histogram (MACD line − Signal line)',
    },
    {
      value: 'BB_UPPER', label: 'Bollinger Upper', unit: '₹', needsTimeframe: true,
      params: [
        { key: 'period', label: 'Period', default: 20,  min: 2 },
        { key: 'std',    label: 'Std Dev', default: 2,  min: 0.5, step: 0.5 },
      ],
    },
    {
      value: 'BB_LOWER', label: 'Bollinger Lower', unit: '₹', needsTimeframe: true,
      params: [
        { key: 'period', label: 'Period', default: 20,  min: 2 },
        { key: 'std',    label: 'Std Dev', default: 2,  min: 0.5, step: 0.5 },
      ],
    },
    {
      value: 'VWAP', label: 'VWAP', unit: '₹', needsTimeframe: true,
      params: [],
      description: 'Volume Weighted Average Price',
    },
    {
      value: 'ATR', label: 'ATR', unit: 'pts', needsTimeframe: true,
      params: [{ key: 'period', label: 'Period', default: 14, min: 2 }],
      description: 'Average True Range (volatility)',
    },
    {
      value: 'ADX', label: 'ADX', unit: '', needsTimeframe: true,
      params: [{ key: 'period', label: 'Period', default: 14, min: 2 }],
      description: 'Average Directional Index (trend strength 0–100)',
    },
    {
      value: 'STOCH_K', label: 'Stochastic %K', unit: '', needsTimeframe: true,
      params: [
        { key: 'k', label: 'K', default: 14, min: 1 },
        { key: 'd', label: 'D', default: 3,  min: 1 },
      ],
      description: 'Stochastic Oscillator (0–100)',
    },
  ],
  CANDLE: [
    {
      value: 'OPEN',       label: 'Open (₹)',         unit: '₹', needsTimeframe: true,  params: [],
      description: 'Opening price of the candle',
    },
    {
      value: 'HIGH',       label: 'High (₹)',         unit: '₹', needsTimeframe: true,  params: [],
      description: 'Highest price of the candle',
    },
    {
      value: 'LOW',        label: 'Low (₹)',          unit: '₹', needsTimeframe: true,  params: [],
      description: 'Lowest price of the candle',
    },
    {
      value: 'CLOSE',      label: 'Close (₹)',        unit: '₹', needsTimeframe: true,  params: [],
      description: 'Closing price of the candle (LTP for live candle)',
    },
    {
      value: 'VOLUME',     label: 'Volume',           unit: '',  needsTimeframe: true,  params: [],
      description: 'Total traded volume in the candle',
    },
    {
      value: 'PREV_CLOSE', label: 'Prev Close (₹)',   unit: '₹', needsTimeframe: true,  params: [],
      description: 'Closing price of the previous completed candle',
    },
    {
      value: 'BODY_SIZE',  label: 'Body Size (₹)',    unit: '₹', needsTimeframe: true,  params: [],
      description: '|Close − Open| — absolute candle body height',
    },
    {
      value: 'UPPER_SHADOW', label: 'Upper Shadow (₹)', unit: '₹', needsTimeframe: true, params: [],
      description: 'High − max(Open, Close)',
    },
    {
      value: 'LOWER_SHADOW', label: 'Lower Shadow (₹)', unit: '₹', needsTimeframe: true, params: [],
      description: 'min(Open, Close) − Low',
    },
    {
      value: 'CHG_FROM_OPEN', label: 'Change from Open %', unit: '%', needsTimeframe: true, params: [],
      description: '(Close − Open) / Open × 100',
    },
    {
      value: 'CHG_FROM_PREV', label: 'Change from Prev Close %', unit: '%', needsTimeframe: true, params: [],
      description: '(Close − Prev Close) / Prev Close × 100',
    },
  ],
}

// ── Kept for backward compat ──────────────────────────────────────────────────

export const METRIC_OPTIONS: Record<ConditionScope, Array<{ value: string; label: string }>> =
  Object.fromEntries(
    Object.entries(METRIC_CONFIGS).map(([scope, cfgs]) => [
      scope,
      cfgs.map(c => ({ value: c.value, label: c.label })),
    ])
  ) as Record<ConditionScope, Array<{ value: string; label: string }>>

// ── Timeframes ────────────────────────────────────────────────────────────────

export const TIMEFRAME_OPTIONS: Array<{ value: Timeframe; label: string }> = [
  { value: '1m',  label: '1 min'   },
  { value: '3m',  label: '3 min'   },
  { value: '5m',  label: '5 min'   },
  { value: '15m', label: '15 min'  },
  { value: '75m', label: '75 min'  },
  { value: '1d',  label: 'Daily'   },
  { value: '1w',  label: 'Weekly'  },
  { value: '1M',  label: 'Monthly' },
]

// ── Operators ─────────────────────────────────────────────────────────────────

export const OPERATOR_OPTIONS: Array<{ value: ConditionOperator; label: string }> = [
  { value: 'GTE',         label: 'is ≥'           },
  { value: 'GT',          label: 'is >'           },
  { value: 'LTE',         label: 'is ≤'           },
  { value: 'LT',          label: 'is <'           },
  { value: 'EQ',          label: 'is ='           },
  { value: 'CROSS_ABOVE', label: 'crosses above'  },
  { value: 'CROSS_BELOW', label: 'crosses below'  },
]

export const CROSS_OPERATORS = new Set<ConditionOperator>(['CROSS_ABOVE', 'CROSS_BELOW'])

// ── Core data types ───────────────────────────────────────────────────────────

export interface Condition {
  id:        string
  scope:     ConditionScope
  metric:    string
  operator:  ConditionOperator
  value:     number | null
  leg_id:    string | null
  timeframe: Timeframe | null
  params:    Record<string, number>
}

export interface ConditionGroup {
  id:         string
  op:         'AND' | 'OR'
  conditions: Condition[]
  groups:     ConditionGroup[]
}

export interface AlertRuleBuilderData {
  alert_id?:        string
  strategy_id:      string
  name:             string
  description:      string
  is_active:        boolean
  trigger_once:     boolean
  cooldown_secs:    number
  triggered_count?: number
  last_triggered?:  string | null
  notify_popup:     boolean
  notify_telegram:  boolean
  notify_email:     boolean
  notify_webhook:   boolean
  notify_sound:     boolean
  webhook_url:      string
  telegram_chat_id: string
  condition_tree:   ConditionGroup
  created_at?:      string
  updated_at?:      string
}

// ── Factory helpers ───────────────────────────────────────────────────────────

export function defaultCondition(scope: ConditionScope = 'STRATEGY'): Condition {
  const firstMetric = METRIC_CONFIGS[scope][0]
  const defaultTF: Timeframe = scope === 'CANDLE' ? '15m' : '15m'
  return {
    id:        crypto.randomUUID(),
    scope,
    metric:    firstMetric.value,
    operator:  'LTE',
    value:     scope === 'STRATEGY' ? -3000 : 0,
    leg_id:    null,
    timeframe: firstMetric.needsTimeframe ? defaultTF : null,
    params:    Object.fromEntries((firstMetric.params ?? []).map(p => [p.key, p.default])),
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

// ── Natural-language preview ──────────────────────────────────────────────────

function conditionNL(c: Condition): string {
  const metricCfg = METRIC_CONFIGS[c.scope]?.find(m => m.value === c.metric)
  const opLabel   = OPERATOR_OPTIONS.find(o => o.value === c.operator)?.label ?? c.operator
  const unit      = metricCfg?.unit ?? ''

  // Build subject
  let subject = ''
  if (c.scope === 'STRATEGY') {
    subject = `Strategy ${metricCfg?.label ?? c.metric}`
  } else if (c.scope === 'LEG') {
    subject = `Leg [${c.leg_id ? c.leg_id.slice(0, 6) : '?'}] ${metricCfg?.label ?? c.metric}`
  } else if (c.scope === 'SPOT') {
    subject = metricCfg?.label ?? c.metric
  } else if (c.scope === 'INDICATOR') {
    const paramStr = (metricCfg?.params ?? [])
      .map(p => `${p.label.charAt(0)}:${c.params[p.key] ?? p.default}`)
      .join(', ')
    const tf = c.timeframe ?? '15m'
    subject = `${metricCfg?.label ?? c.metric}${paramStr ? `(${paramStr})` : ''} [${tf}]`
  } else if (c.scope === 'CANDLE') {
    const tf = c.timeframe ?? '15m'
    subject = `Candle ${metricCfg?.label ?? c.metric} [${tf}]`
  }

  if (CROSS_OPERATORS.has(c.operator)) {
    return `${subject} ${opLabel}`
  }
  return `${subject} ${opLabel} ${c.value ?? 0}${unit}`
}

function groupNL(g: ConditionGroup, depth = 0): string {
  const parts: string[] = [
    ...g.conditions.map(c => conditionNL(c)),
    ...g.groups.map(sg => `(${groupNL(sg, depth + 1)})`),
  ]
  return parts.join(` ${g.op} `)
}

export function buildPreviewText(tree: ConditionGroup): string {
  return groupNL(tree, 0) || '(no conditions)'
}
