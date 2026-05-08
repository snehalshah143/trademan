import { useState, useCallback } from 'react'
import { Plus, Trash2, ChevronDown, ChevronUp, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import type {
  AlertRuleBuilderData,
  Condition,
  ConditionGroup,
  ConditionScope,
  ConditionOperator,
  RHSType,
  Timeframe,
} from '@/types/alertRules'
import {
  METRIC_CONFIGS,
  OPERATOR_OPTIONS,
  TIMEFRAME_OPTIONS,
  INDICATOR_SOURCE_OPTIONS,
  CROSS_OPERATORS,
  defaultCondition,
  defaultGroup,
  buildPreviewText,
} from '@/types/alertRules'

// ── Shared style tokens ───────────────────────────────────────────────────────

// Chip-style inline select (Chartink-like colored tokens)
const chipBase =
  'inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold appearance-none ' +
  'cursor-pointer focus:outline-none border transition-colors'
const chipBlue   = `${chipBase} bg-accent-blue/10   border-accent-blue/30   text-accent-blue   hover:bg-accent-blue/20`
const chipAmber  = `${chipBase} bg-accent-amber/10  border-accent-amber/30  text-accent-amber  hover:bg-accent-amber/20`
const chipPurple = `${chipBase} bg-accent-purple/10 border-accent-purple/30 text-accent-purple hover:bg-accent-purple/20`
const chipGreen  = `${chipBase} bg-profit/10        border-profit/30        text-profit        hover:bg-profit/20`

const paramInput =
  'w-12 text-center text-[11px] text-text-primary bg-surface-3 border border-border-subtle ' +
  'rounded px-1 py-0.5 focus:outline-none focus:border-accent-blue tabular-nums'

const numInput =
  'w-20 text-[11px] text-text-primary bg-surface-3 border border-border-default ' +
  'rounded px-2 py-0.5 focus:outline-none focus:border-profit tabular-nums'

// Used in main AlertRuleBuilder (name/cooldown fields)
const numCls =
  'bg-surface-3 border border-border-default text-text-primary text-xs rounded px-2 py-1.5 ' +
  'focus:outline-none focus:border-accent-blue tabular-nums w-full'

const colLabelCls = 'text-[9px] text-text-muted uppercase tracking-widest font-semibold mb-1.5'

// ── Leg type ─────────────────────────────────────────────────────────────────

interface Leg {
  leg_id: string
  symbol: string
  side:   'BUY' | 'SELL'
}

// ── Target option (combines scope + leg_id into a single selector) ─────────

interface TargetOption {
  value:  string
  label:  string
  scope:  ConditionScope
  leg_id: string | null
}

function buildTargetOptions(legs: Leg[]): TargetOption[] {
  return [
    { value: 'STRATEGY',  label: 'Strategy',                    scope: 'STRATEGY',  leg_id: null },
    ...legs.map((l, i) => ({
      value:  `LEG:${l.leg_id}`,
      label:  `Leg ${i + 1} (${l.side})`,
      scope:  'LEG' as ConditionScope,
      leg_id: l.leg_id,
    })),
    { value: 'SPOT',      label: 'Underlying Spot',             scope: 'SPOT',      leg_id: null },
    { value: 'CANDLE',    label: 'Underlying OHLCV (Candle)',   scope: 'CANDLE',    leg_id: null },
    { value: 'INDICATOR', label: 'Indicator on Underlying',     scope: 'INDICATOR', leg_id: null },
  ]
}

// ── Timeframe short-label (Chartink-style prefix) ─────────────────────────────

const TF_SHORT: Record<string, string> = {
  '1m': 'M1', '3m': 'M3', '5m': 'M5', '15m': 'M15',
  '75m': 'M75', '1d': 'Daily', '1w': 'Weekly', '1M': 'Monthly',
}
const tfShort = (tf: string | null) => TF_SHORT[tf ?? '15m'] ?? tf ?? 'M15'

// ── RHS type helpers ──────────────────────────────────────────────────────────

const RHS_TYPE_OPTIONS: Array<{ value: RHSType; label: string }> = [
  { value: 'NUMBER',    label: '123'       },
  { value: 'SPOT',      label: 'Spot'      },
  { value: 'CANDLE',    label: 'OHLCV'     },
  { value: 'INDICATOR', label: 'Indicator' },
]

// STRATEGY and LEG only make sense vs a fixed number
function availableRHSTypes(scope: ConditionScope): RHSType[] {
  if (scope === 'STRATEGY' || scope === 'LEG') return ['NUMBER']
  return ['NUMBER', 'SPOT', 'CANDLE', 'INDICATOR']
}

// Candle metrics usable as RHS (price fields only)
const CANDLE_RHS_METRICS = ['OPEN', 'HIGH', 'LOW', 'CLOSE', 'PREV_CLOSE']

// ── Single condition row (Chartink sentence-chip style) ───────────────────────

interface ConditionCardProps {
  condition:    Condition
  positionLegs: Leg[]
  onChange:     (c: Condition) => void
  onRemove:     () => void
}

function ConditionCard({ condition, positionLegs, onChange, onRemove }: ConditionCardProps) {
  const targetOptions = buildTargetOptions(positionLegs)

  const currentTargetValue =
    condition.scope === 'LEG' && condition.leg_id
      ? `LEG:${condition.leg_id}`
      : condition.scope

  const metricCfgs = METRIC_CONFIGS[condition.scope]
  const metricCfg  = metricCfgs.find(m => m.value === condition.metric) ?? metricCfgs[0]

  // All scopes support crosses above/below — e.g. "MTM crosses above 5000" is valid
  // (edge trigger: fires once on transition, unlike ≥ which fires continuously)
  const availableOperators = OPERATOR_OPTIONS

  // RHS state
  const rhsType    = condition.rhs_type ?? 'NUMBER'
  const rhsOptions = availableRHSTypes(condition.scope)
  const rhsCfgInd  = METRIC_CONFIGS.INDICATOR.find(m => m.value === (condition.rhs_metric || 'EMA'))

  // ── handlers ──────────────────────────────────────────────────────────────

  const handleTargetChange = (targetValue: string) => {
    const opt = targetOptions.find(o => o.value === targetValue)
    if (!opt) return
    const first             = METRIC_CONFIGS[opt.scope][0]
    const scopeAllowsIndRHS = !(['STRATEGY', 'LEG'] as ConditionScope[]).includes(opt.scope)
    onChange({
      ...condition,
      scope:     opt.scope,
      leg_id:    opt.leg_id,
      metric:    first.value,
      // keep existing operator — all operators valid for all scopes now
      operator:  condition.operator,
      timeframe: first.needsTimeframe ? (condition.timeframe ?? '15m') : null,
      params:    Object.fromEntries((first.params ?? []).map(p => [p.key, p.default])),
      rhs_type:  scopeAllowsIndRHS ? rhsType : 'NUMBER',
    })
  }

  const handleMetricChange = (metric: string) => {
    const cfg = METRIC_CONFIGS[condition.scope].find(m => m.value === metric) ?? metricCfg
    onChange({
      ...condition,
      metric,
      timeframe: cfg.needsTimeframe ? (condition.timeframe ?? '15m') : null,
      params:    Object.fromEntries((cfg.params ?? []).map(p => [p.key, p.default])),
    })
  }

  const handleOperatorChange = (op: ConditionOperator) => {
    const newIsCross = CROSS_OPERATORS.has(op)
    // For scopes that support indicator RHS (SPOT/CANDLE/INDICATOR), auto-switch RHS to
    // INDICATOR when a cross operator is picked — makes the common case one click.
    // For STRATEGY/LEG, cross vs a NUMBER makes sense (e.g. MTM crosses above 5000).
    if (newIsCross && rhsType === 'NUMBER' && rhsOptions.includes('INDICATOR')) {
      const defaultEMA = METRIC_CONFIGS.INDICATOR.find(m => m.value === 'EMA')!
      onChange({
        ...condition,
        operator:      op,
        rhs_type:      'INDICATOR',
        rhs_metric:    'EMA',
        rhs_timeframe: '15m',
        rhs_params:    Object.fromEntries(defaultEMA.params.map(p => [p.key, p.default])),
      })
    } else {
      onChange({ ...condition, operator: op })
    }
  }

  const handleRHSTypeChange = (type: RHSType) => {
    const defaultMetric =
      type === 'INDICATOR' ? 'EMA' :
      type === 'SPOT'      ? 'SPOT_PRICE' :
      type === 'CANDLE'    ? 'CLOSE' : ''
    const indCfg = type === 'INDICATOR'
      ? METRIC_CONFIGS.INDICATOR.find(m => m.value === 'EMA')!
      : null
    onChange({
      ...condition,
      rhs_type:      type,
      rhs_metric:    defaultMetric,
      rhs_timeframe: (type === 'INDICATOR' || type === 'CANDLE') ? '15m' : null,
      rhs_params:    indCfg ? Object.fromEntries(indCfg.params.map(p => [p.key, p.default])) : {},
    })
  }

  const handleRHSIndicatorChange = (metric: string) => {
    const cfg = METRIC_CONFIGS.INDICATOR.find(m => m.value === metric)
    onChange({
      ...condition,
      rhs_metric: metric,
      rhs_params: Object.fromEntries((cfg?.params ?? []).map(p => [p.key, p.default])),
      rhs_source: 'CLOSE', // reset source when indicator changes
    })
  }

  const handleParam = (key: string, val: number) =>
    onChange({ ...condition, params: { ...condition.params, [key]: val } })

  const handleRHSParam = (key: string, val: number) =>
    onChange({ ...condition, rhs_params: { ...condition.rhs_params, [key]: val } })

  // ── render (Chartink sentence-chip style) ─────────────────────────────────

  return (
    <div className="flex items-center gap-1.5 flex-wrap px-3 py-2.5 rounded-lg bg-surface-2 border border-border-default group min-h-[42px]">

      {/* LHS: Target chip */}
      <select value={currentTargetValue} onChange={e => handleTargetChange(e.target.value)} className={chipBlue}>
        {targetOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>

      {/* LHS: Metric chip */}
      <select value={condition.metric} onChange={e => handleMetricChange(e.target.value)} className={chipBlue} title={metricCfg?.description}>
        {METRIC_CONFIGS[condition.scope].map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
      </select>

      {/* LHS: Source selector — "of [M15 Close ▾]" — for EMA/SMA. Appears BEFORE params
           so it reads: EMA of M15 Close (21) [15min] — matching Chartink pattern */}
      {metricCfg?.supportsSource && (
        <>
          <span className="text-[10px] text-text-muted select-none">of</span>
          <select
            value={condition.lhs_source ?? 'CLOSE'}
            onChange={e => onChange({ ...condition, lhs_source: e.target.value })}
            className={chipGreen}
          >
            {INDICATOR_SOURCE_OPTIONS.map(s => (
              <option key={s.value} value={s.value}>
                {tfShort(condition.timeframe)} {s.label}
              </option>
            ))}
          </select>
        </>
      )}

      {/* LHS: Inline params — e.g. (21) */}
      {metricCfg && metricCfg.params.length > 0 && (
        <span className="inline-flex items-center gap-0.5 text-[11px] text-text-muted">
          {'('}
          {metricCfg.params.map((p, i) => (
            <span key={p.key} className="inline-flex items-center gap-0.5">
              {i > 0 && <span className="mx-0.5">,</span>}
              <input
                type="number"
                value={condition.params[p.key] ?? p.default}
                onChange={e => handleParam(p.key, Number(e.target.value))}
                min={p.min} max={p.max} step={p.step ?? 1}
                title={p.label}
                className={paramInput}
              />
            </span>
          ))}
          {')'}
        </span>
      )}

      {/* LHS: Timeframe chip (for indicators/candles) */}
      {metricCfg?.needsTimeframe && (
        <select value={condition.timeframe ?? '15m'} onChange={e => onChange({ ...condition, timeframe: e.target.value as Timeframe })} className={chipPurple}>
          {TIMEFRAME_OPTIONS.map(tf => <option key={tf.value} value={tf.value}>{tf.label}</option>)}
        </select>
      )}

      {/* OPERATOR chip — includes crosses above / crosses below */}
      <select value={condition.operator} onChange={e => handleOperatorChange(e.target.value as ConditionOperator)} className={chipAmber}>
        {availableOperators.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>

      {/* RHS type toggle — only shown when multiple types are available */}
      {rhsOptions.length > 1 && (
        <div className="inline-flex rounded overflow-hidden border border-border-subtle">
          {RHS_TYPE_OPTIONS.filter(o => rhsOptions.includes(o.value)).map(opt => (
            <button
              key={opt.value}
              type="button"
              onClick={() => handleRHSTypeChange(opt.value)}
              className={cn(
                'px-1.5 py-0.5 text-[9px] font-semibold transition-colors',
                rhsType === opt.value
                  ? 'bg-profit/20 text-profit'
                  : 'bg-surface-3 text-text-muted hover:text-text-secondary'
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}

      {/* RHS: NUMBER */}
      {rhsType === 'NUMBER' && (
        <input
          type="number"
          value={condition.value ?? ''}
          onChange={e => onChange({ ...condition, value: e.target.value === '' ? null : Number(e.target.value) })}
          placeholder="0"
          className={numInput}
        />
      )}

      {/* RHS: SPOT metric chip */}
      {rhsType === 'SPOT' && (
        <select value={condition.rhs_metric || 'SPOT_PRICE'} onChange={e => onChange({ ...condition, rhs_metric: e.target.value })} className={chipGreen}>
          {METRIC_CONFIGS.SPOT.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
      )}

      {/* RHS: CANDLE metric + timeframe chips */}
      {rhsType === 'CANDLE' && (
        <>
          <select value={condition.rhs_metric || 'CLOSE'} onChange={e => onChange({ ...condition, rhs_metric: e.target.value })} className={chipGreen}>
            {METRIC_CONFIGS.CANDLE.filter(m => CANDLE_RHS_METRICS.includes(m.value)).map(m => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </select>
          <select value={condition.rhs_timeframe || '15m'} onChange={e => onChange({ ...condition, rhs_timeframe: e.target.value as Timeframe })} className={chipPurple}>
            {TIMEFRAME_OPTIONS.map(tf => <option key={tf.value} value={tf.value}>{tf.label}</option>)}
          </select>
        </>
      )}

      {/* RHS: INDICATOR chip + source + inline params + timeframe — e.g. EMA of RSI(14) (21) 15min */}
      {rhsType === 'INDICATOR' && (
        <>
          <select value={condition.rhs_metric || 'EMA'} onChange={e => handleRHSIndicatorChange(e.target.value)} className={chipGreen}>
            {METRIC_CONFIGS.INDICATOR.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
          {/* RHS source — shown when RHS indicator supports source (EMA/SMA).
               Appears BEFORE params: EMA of M15 Close (21) [15min] */}
          {rhsCfgInd?.supportsSource && (
            <>
              <span className="text-[10px] text-text-muted select-none">of</span>
              <select
                value={condition.rhs_source ?? 'CLOSE'}
                onChange={e => onChange({ ...condition, rhs_source: e.target.value })}
                className={chipGreen}
              >
                {INDICATOR_SOURCE_OPTIONS.map(s => (
                  <option key={s.value} value={s.value}>
                    {tfShort(condition.rhs_timeframe)} {s.label}
                  </option>
                ))}
              </select>
            </>
          )}
          {rhsCfgInd && rhsCfgInd.params.length > 0 && (
            <span className="inline-flex items-center gap-0.5 text-[11px] text-text-muted">
              {'('}
              {rhsCfgInd.params.map((p, i) => (
                <span key={p.key} className="inline-flex items-center gap-0.5">
                  {i > 0 && <span className="mx-0.5">,</span>}
                  <input
                    type="number"
                    value={condition.rhs_params?.[p.key] ?? p.default}
                    onChange={e => handleRHSParam(p.key, Number(e.target.value))}
                    min={p.min} max={p.max} step={p.step ?? 1}
                    title={p.label}
                    className={paramInput}
                  />
                </span>
              ))}
              {')'}
            </span>
          )}
          <select value={condition.rhs_timeframe || '15m'} onChange={e => onChange({ ...condition, rhs_timeframe: e.target.value as Timeframe })} className={chipPurple}>
            {TIMEFRAME_OPTIONS.map(tf => <option key={tf.value} value={tf.value}>{tf.label}</option>)}
          </select>
        </>
      )}

      {/* Remove button — appears on hover */}
      <button
        type="button"
        onClick={onRemove}
        className="ml-auto p-1 text-text-muted hover:text-loss rounded hover:bg-loss/10 transition-colors opacity-0 group-hover:opacity-100"
        title="Remove condition"
      >
        <X size={12} />
      </button>
    </div>
  )
}

// ── Condition group block — Chartink-style ────────────────────────────────────

interface GroupBlockProps {
  group:        ConditionGroup
  positionLegs: Leg[]
  onChange:     (g: ConditionGroup) => void
  onRemove?:    () => void
  depth?:       number
}

function GroupBlock({
  group,
  positionLegs,
  onChange,
  onRemove,
  depth = 0,
}: GroupBlockProps) {
  const toggleOp = () =>
    onChange({ ...group, op: group.op === 'AND' ? 'OR' : 'AND' })

  const updateCondition = (idx: number, c: Condition) => {
    const conditions = [...group.conditions]
    conditions[idx] = c
    onChange({ ...group, conditions })
  }
  const removeCondition = (idx: number) =>
    onChange({ ...group, conditions: group.conditions.filter((_, i) => i !== idx) })
  const addCondition = (scope: ConditionScope = 'STRATEGY') =>
    onChange({ ...group, conditions: [...group.conditions, defaultCondition(scope)] })

  const updateNestedGroup = (idx: number, g: ConditionGroup) => {
    const groups = [...group.groups]
    groups[idx] = g
    onChange({ ...group, groups })
  }
  const removeNestedGroup = (idx: number) =>
    onChange({ ...group, groups: group.groups.filter((_, i) => i !== idx) })
  const addNestedGroup = () =>
    onChange({ ...group, groups: [...group.groups, defaultGroup()] })

  const totalItems = group.conditions.length + group.groups.length

  const borderCls =
    depth === 0 ? 'border-border-strong' :
    depth === 1 ? 'border-accent-blue/40' :
    'border-accent-purple/40'

  return (
    <div className={cn('rounded-lg border overflow-hidden', borderCls)}>

      {/* ── Chartink-style group header ─────────────────── */}
      <div className="flex items-center gap-2 px-4 py-2 bg-surface-3/60 border-b border-border-subtle">
        {depth > 0 && (
          <span className="text-[10px] text-text-muted uppercase tracking-widest font-semibold">Sub-group</span>
        )}
        <span className="text-xs text-text-secondary">Passes</span>
        {/* ALL / ANY toggle — click to switch */}
        <button
          type="button"
          onClick={toggleOp}
          title="Click to toggle ALL / ANY"
          className={cn(
            'px-2.5 py-0.5 text-xs font-bold rounded-full border transition-colors',
            group.op === 'AND'
              ? 'bg-accent-blue/15 border-accent-blue/40 text-accent-blue hover:bg-accent-blue/25'
              : 'bg-accent-amber/15 border-accent-amber/40 text-accent-amber hover:bg-accent-amber/25'
          )}
        >
          {group.op === 'AND' ? 'ALL' : 'ANY'}
        </button>
        <span className="text-xs text-text-secondary">of the following conditions</span>
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            className="ml-auto p-1 text-text-muted hover:text-loss transition-colors"
            title="Remove sub-group"
          >
            <Trash2 size={11} />
          </button>
        )}
      </div>

      {/* ── Conditions list ──────────────────────────────── */}
      <div className="p-3 space-y-2 bg-surface-1">

        {group.conditions.map((cond, i) => (
          <ConditionCard
            key={cond.id}
            condition={cond}
            positionLegs={positionLegs}
            onChange={c => updateCondition(i, c)}
            onRemove={() => removeCondition(i)}
          />
        ))}

        {group.groups.map((sub, i) => (
          <GroupBlock
            key={sub.id}
            group={sub}
            positionLegs={positionLegs}
            onChange={g => updateNestedGroup(i, g)}
            onRemove={() => removeNestedGroup(i)}
            depth={depth + 1}
          />
        ))}

        {totalItems === 0 && (
          <div className="text-xs text-text-muted italic text-center py-3">
            No conditions yet — add one below.
          </div>
        )}

        {/* ── Add buttons ─────────────────────────────────── */}
        <div className="flex items-center gap-1.5 flex-wrap pt-1.5 mt-1 border-t border-border-subtle">
          <span className="text-[10px] text-text-muted uppercase tracking-widest font-semibold mr-1">+ Add:</span>
          {(
            [
              { scope: 'STRATEGY'  as ConditionScope, label: 'Strategy'  },
              { scope: 'LEG'       as ConditionScope, label: 'Leg'       },
              { scope: 'SPOT'      as ConditionScope, label: 'Spot Price' },
              { scope: 'CANDLE'    as ConditionScope, label: 'OHLCV'     },
              { scope: 'INDICATOR' as ConditionScope, label: 'Indicator' },
            ] as const
          ).map(({ scope, label }) => (
            <button
              key={scope}
              type="button"
              onClick={() => addCondition(scope)}
              className="inline-flex items-center gap-0.5 text-[11px] text-text-muted hover:text-accent-blue border border-border-subtle hover:border-accent-blue/40 rounded px-2 py-0.5 transition-colors"
            >
              <Plus size={9} />
              {label}
            </button>
          ))}
          {depth < 2 && (
            <button
              type="button"
              onClick={addNestedGroup}
              className="inline-flex items-center gap-0.5 text-[11px] text-text-muted hover:text-accent-purple border border-border-subtle hover:border-accent-purple/40 rounded px-2 py-0.5 transition-colors"
            >
              <Plus size={9} />
              Sub-group
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Notification toggle pill ──────────────────────────────────────────────────

function NotifToggle({
  label,
  checked,
  onChange,
}: {
  label:    string
  checked:  boolean
  onChange: (v: boolean) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={cn(
        'px-3 py-1.5 text-xs rounded-md border transition-colors',
        checked
          ? 'border-accent-blue bg-accent-blue/10 text-accent-blue'
          : 'border-border-default text-text-muted hover:border-border-strong'
      )}
    >
      {label}
    </button>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface AlertRuleBuilderProps {
  strategyId:   string
  positionLegs: Leg[]
  initialData:  AlertRuleBuilderData
  onSave:       (data: AlertRuleBuilderData) => void
  onCancel:     () => void
  isSaving?:    boolean
}

export function AlertRuleBuilder({
  strategyId: _strategyId,
  positionLegs,
  initialData,
  onSave,
  onCancel,
  isSaving = false,
}: AlertRuleBuilderProps) {
  const [form,        setForm       ] = useState<AlertRuleBuilderData>(initialData)
  const [previewOpen, setPreviewOpen] = useState(false)

  const update = useCallback((patch: Partial<AlertRuleBuilderData>) => {
    setForm(prev => ({ ...prev, ...patch }))
  }, [])

  const setTree = useCallback((tree: ConditionGroup) => {
    setForm(prev => ({ ...prev, condition_tree: tree }))
  }, [])

  const handleSave = () => {
    if (!form.name.trim()) return
    onSave(form)
  }

  const previewText = buildPreviewText(form.condition_tree)

  return (
    <div className="flex flex-col gap-0 overflow-y-auto custom-scroll max-h-[80vh]">

      {/* ── Row 1: Name + trigger meta ──────────────────────────────────── */}
      <div className="px-5 pt-4 pb-3 border-b border-border-subtle bg-surface-2/50">
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-1">
            <label className={colLabelCls}>Alert Name *</label>
            <input
              value={form.name}
              onChange={e => update({ name: e.target.value })}
              placeholder="e.g. MTM Stop Loss"
              className={cn(numCls, 'w-full')}
            />
          </div>
          <div>
            <label className={colLabelCls}>Trigger mode</label>
            <button
              type="button"
              onClick={() => update({ trigger_once: !form.trigger_once })}
              className={cn(
                'w-full px-2 py-1.5 text-xs rounded border transition-colors text-left',
                form.trigger_once
                  ? 'border-accent-amber bg-accent-amber/10 text-accent-amber'
                  : 'border-border-default text-text-muted hover:border-border-strong'
              )}
            >
              {form.trigger_once ? 'Once only (auto-disable)' : 'Every time condition is met'}
            </button>
          </div>
          <div>
            <label className={colLabelCls}>Cooldown (seconds)</label>
            <input
              type="number"
              min={0}
              value={form.cooldown_secs}
              onChange={e => update({ cooldown_secs: Math.max(0, Number(e.target.value)) })}
              className={cn(numCls, 'w-full')}
              placeholder="60"
            />
          </div>
        </div>
      </div>

      {/* ── Condition builder ────────────────────────────────────────────── */}
      <div className="px-5 py-4 space-y-3">
        <div className="flex items-center gap-2 mb-1">
          <span className={cn(colLabelCls, 'mb-0')}>Conditions</span>
          <span className="text-[10px] text-text-muted">
            Click <span className="font-bold text-accent-blue">ALL</span> / <span className="font-bold text-accent-amber">ANY</span> in the header to toggle the group operator
          </span>
        </div>

        <GroupBlock
          group={form.condition_tree}
          positionLegs={positionLegs}
          onChange={setTree}
          depth={0}
        />
      </div>

      {/* ── Notifications ────────────────────────────────────────────────── */}
      <div className="px-5 py-3 border-t border-border-subtle bg-surface-2/30">
        <div className={cn(colLabelCls, 'mb-2')}>Notify via</div>
        <div className="flex flex-wrap gap-2">
          <NotifToggle label="Popup"    checked={form.notify_popup}    onChange={v => update({ notify_popup: v })} />
          <NotifToggle label="Sound"    checked={form.notify_sound}    onChange={v => update({ notify_sound: v })} />
          <NotifToggle label="Telegram" checked={form.notify_telegram} onChange={v => update({ notify_telegram: v })} />
          <NotifToggle label="Email"    checked={form.notify_email}    onChange={v => update({ notify_email: v })} />
          <NotifToggle label="Webhook"  checked={form.notify_webhook}  onChange={v => update({ notify_webhook: v })} />
        </div>
        {form.notify_telegram && (
          <input
            value={form.telegram_chat_id}
            onChange={e => update({ telegram_chat_id: e.target.value })}
            placeholder="Telegram Chat ID"
            className={cn(numCls, 'w-full mt-2')}
          />
        )}
        {form.notify_webhook && (
          <input
            value={form.webhook_url}
            onChange={e => update({ webhook_url: e.target.value })}
            placeholder="Webhook URL  https://…"
            className={cn(numCls, 'w-full mt-2')}
          />
        )}
      </div>

      {/* ── Natural-language preview ─────────────────────────────────────── */}
      <div className="px-5 py-3 border-t border-border-subtle">
        <button
          type="button"
          onClick={() => setPreviewOpen(p => !p)}
          className="flex items-center gap-1.5 text-[10px] text-text-muted uppercase tracking-wide mb-1 hover:text-text-secondary transition-colors"
        >
          Condition preview
          {previewOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
        </button>
        {previewOpen && (
          <pre className="bg-surface-0 border border-border-subtle rounded p-3 text-[11px] text-text-secondary font-mono whitespace-pre-wrap leading-5">
            {previewText}
          </pre>
        )}
      </div>

      {/* ── Footer ───────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-end gap-3 px-5 py-3 border-t border-border-subtle bg-surface-2/50">
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-1.5 text-xs text-text-muted hover:text-text-primary border border-border-default rounded-md transition-colors"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={!form.name.trim() || isSaving}
          className="px-5 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {isSaving ? 'Saving…' : 'Save Alert Rule'}
        </button>
      </div>
    </div>
  )
}
