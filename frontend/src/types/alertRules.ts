// ─── Types for the Alert Rule Builder (Chartink-style condition tree) ────────

export type ConditionScope = 'STRATEGY' | 'LEG' | 'SPOT' | 'INDICATOR' | 'CANDLE'

// RHS of a condition: fixed number OR another indicator/candle/spot value
export type RHSType = 'NUMBER' | 'SPOT' | 'CANDLE' | 'INDICATOR'

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
  value:          string
  label:          string
  unit:           '₹' | '%' | '' | 'pts'
  needsTimeframe: boolean
  params:         ParamDef[]
  description?:   string
  supportsSource?: boolean  // true for EMA, SMA — lets user pick what the indicator is computed on
}

// ── Indicator source options (what an EMA/SMA is applied to) ──────────────────

export interface SourceOption {
  value: string
  label: string
  group: 'Price' | 'Indicator'
}

export const INDICATOR_SOURCE_OPTIONS: SourceOption[] = [
  // Price-based sources
  { value: 'CLOSE',   label: 'Close',         group: 'Price'     },
  { value: 'OPEN',    label: 'Open',          group: 'Price'     },
  { value: 'HIGH',    label: 'High',          group: 'Price'     },
  { value: 'LOW',     label: 'Low',           group: 'Price'     },
  { value: 'HL2',     label: '(H+L) / 2',     group: 'Price'     },
  { value: 'HLC3',    label: '(H+L+C) / 3',   group: 'Price'     },
  { value: 'OHLC4',   label: '(O+H+L+C) / 4', group: 'Price'     },
  // Indicator-based sources (chained)
  { value: 'RSI_3',   label: 'RSI (3)',        group: 'Indicator' },
  { value: 'RSI_9',   label: 'RSI (9)',        group: 'Indicator' },
  { value: 'RSI_14',  label: 'RSI (14)',       group: 'Indicator' },
  { value: 'RSI_21',  label: 'RSI (21)',       group: 'Indicator' },
  { value: 'ATR_14',  label: 'ATR (14)',       group: 'Indicator' },
  { value: 'ATR_7',   label: 'ATR (7)',        group: 'Indicator' },
  { value: 'MACD_HIST', label: 'MACD Histogram (12,26,9)', group: 'Indicator' },
  { value: 'STOCH_K',   label: 'Stochastic %K (14,3)',     group: 'Indicator' },
]

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
      supportsSource: true,
    },
    {
      value: 'SMA', label: 'SMA', unit: '₹', needsTimeframe: true,
      params: [{ key: 'period', label: 'Period', default: 20, min: 2, max: 500 }],
      description: 'Simple Moving Average',
      supportsSource: true,
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
  id:            string
  scope:         ConditionScope
  metric:        string
  operator:      ConditionOperator
  value:         number | null
  leg_id:        string | null
  timeframe:     Timeframe | null
  params:        Record<string, number>
  lhs_source:    string           // source for EMA/SMA: 'CLOSE' | 'RSI_14' | etc. (default: 'CLOSE')
  // RHS (right-hand side) — what to compare against
  rhs_type:      RHSType          // default: 'NUMBER'
  rhs_metric:    string           // e.g. 'EMA', 'CLOSE', 'SPOT_PRICE'
  rhs_timeframe: Timeframe | null // for INDICATOR / CANDLE RHS
  rhs_params:    Record<string, number> // e.g. { period: 21 } for EMA
  rhs_source:    string           // source for RHS EMA/SMA (default: 'CLOSE')
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
  const defaultEMA  = METRIC_CONFIGS.INDICATOR.find(m => m.value === 'EMA')!
  return {
    id:            crypto.randomUUID(),
    scope,
    metric:        firstMetric.value,
    operator:      'LTE',
    value:         scope === 'STRATEGY' ? -3000 : 0,
    leg_id:        null,
    timeframe:     firstMetric.needsTimeframe ? '15m' : null,
    params:        Object.fromEntries((firstMetric.params ?? []).map(p => [p.key, p.default])),
    lhs_source:    'CLOSE',
    rhs_type:      'NUMBER',
    rhs_metric:    'EMA',
    rhs_timeframe: '15m',
    rhs_params:    Object.fromEntries(defaultEMA.params.map(p => [p.key, p.default])),
    rhs_source:    'CLOSE',
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

  // Build LHS subject
  let subject = ''
  if (c.scope === 'STRATEGY') {
    subject = `Strategy ${metricCfg?.label ?? c.metric}`
  } else if (c.scope === 'LEG') {
    subject = `Leg [${c.leg_id ? c.leg_id.slice(0, 6) : '?'}] ${metricCfg?.label ?? c.metric}`
  } else if (c.scope === 'SPOT') {
    subject = metricCfg?.label ?? c.metric
  } else if (c.scope === 'INDICATOR') {
    const src      = metricCfg?.supportsSource ? (c.lhs_source ?? 'CLOSE') : null
    const srcLabel = src ? INDICATOR_SOURCE_OPTIONS.find(s => s.value === src)?.label ?? src : null
    const paramStr = (metricCfg?.params ?? [])
      .map(p => `${p.label.charAt(0)}:${c.params[p.key] ?? p.default}`)
      .join(', ')
    const tf = c.timeframe ?? '15m'
    const inner = [srcLabel, paramStr].filter(Boolean).join(', ')
    subject = `${metricCfg?.label ?? c.metric}(${inner}) [${tf}]`
  } else if (c.scope === 'CANDLE') {
    const tf = c.timeframe ?? '15m'
    subject = `Candle ${metricCfg?.label ?? c.metric} [${tf}]`
  }

  // Build RHS
  const rhsType = c.rhs_type ?? 'NUMBER'
  let rhs = ''
  if (rhsType === 'NUMBER') {
    rhs = `${c.value ?? 0}${unit}`
  } else if (rhsType === 'SPOT') {
    const rhsCfg = METRIC_CONFIGS.SPOT.find(m => m.value === c.rhs_metric)
    rhs = rhsCfg?.label ?? c.rhs_metric
  } else if (rhsType === 'CANDLE') {
    const rhsCfg = METRIC_CONFIGS.CANDLE.find(m => m.value === c.rhs_metric)
    rhs = `${rhsCfg?.label ?? c.rhs_metric} [${c.rhs_timeframe ?? '15m'}]`
  } else if (rhsType === 'INDICATOR') {
    const rhsCfg    = METRIC_CONFIGS.INDICATOR.find(m => m.value === c.rhs_metric)
    const rhsSrc    = rhsCfg?.supportsSource ? (c.rhs_source ?? 'CLOSE') : null
    const rhsSrcLbl = rhsSrc ? INDICATOR_SOURCE_OPTIONS.find(s => s.value === rhsSrc)?.label ?? rhsSrc : null
    const paramStr  = (rhsCfg?.params ?? [])
      .map(p => `${p.label.charAt(0)}:${c.rhs_params?.[p.key] ?? p.default}`)
      .join(', ')
    const inner = [rhsSrcLbl, paramStr].filter(Boolean).join(', ')
    rhs = `${rhsCfg?.label ?? c.rhs_metric}(${inner}) [${c.rhs_timeframe ?? '15m'}]`
  }

  return `${subject} ${opLabel} ${rhs}`
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
