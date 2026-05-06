import { useState, useCallback } from 'react'
import { Plus, Trash2, ChevronDown, ChevronUp, X, Info } from 'lucide-react'
import { cn } from '@/lib/utils'
import type {
  AlertRuleBuilderData,
  Condition,
  ConditionGroup,
  ConditionScope,
  ConditionOperator,
  Timeframe,
} from '@/types/alertRules'
import {
  METRIC_CONFIGS,
  OPERATOR_OPTIONS,
  TIMEFRAME_OPTIONS,
  CROSS_OPERATORS,
  defaultCondition,
  defaultGroup,
  buildPreviewText,
} from '@/types/alertRules'

// ── Shared style tokens ───────────────────────────────────────────────────────

const selectCls =
  'bg-surface-3 border border-border-default text-text-primary text-xs rounded px-2 py-1.5 ' +
  'focus:outline-none focus:border-accent-blue w-full appearance-none cursor-pointer'

const numCls =
  'bg-surface-3 border border-border-default text-text-primary text-xs rounded px-2 py-1.5 ' +
  'focus:outline-none focus:border-accent-blue tabular-nums w-full'

const paramCls =
  'bg-surface-4 border border-border-subtle text-text-primary text-xs rounded px-1.5 py-1 ' +
  'focus:outline-none focus:border-accent-blue tabular-nums w-14 text-center'

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
    { value: 'STRATEGY',  label: 'Strategy',             scope: 'STRATEGY',  leg_id: null },
    ...legs.map((l, i) => ({
      value:  `LEG:${l.leg_id}`,
      label:  `Leg ${i + 1} (${l.side})`,
      scope:  'LEG' as ConditionScope,
      leg_id: l.leg_id,
    })),
    { value: 'SPOT',      label: 'Underlying Spot',      scope: 'SPOT',      leg_id: null },
    { value: 'INDICATOR', label: 'Indicator on Underlying', scope: 'INDICATOR', leg_id: null },
  ]
}

// ── AND / OR visual connector ─────────────────────────────────────────────────

function AndOrConnector({
  op,
  onClick,
}: {
  op:      'AND' | 'OR'
  onClick: () => void
}) {
  return (
    <div className="flex items-center gap-3 py-2 select-none">
      <div className="flex-1 h-px bg-border-subtle" />
      <button
        type="button"
        onClick={onClick}
        title="Click to toggle AND / OR"
        className={cn(
          'px-4 py-0.5 rounded-full text-[10px] font-bold tracking-widest border transition-all',
          op === 'AND'
            ? 'border-accent-blue/40 bg-accent-blue/10 text-accent-blue hover:bg-accent-blue/20'
            : 'border-accent-amber/40 bg-accent-amber/10 text-accent-amber hover:bg-accent-amber/20'
        )}
      >
        {op}
      </button>
      <div className="flex-1 h-px bg-border-subtle" />
    </div>
  )
}

// ── Single condition card (Chartink-style horizontal row) ─────────────────────

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
  const isCross    = CROSS_OPERATORS.has(condition.operator)

  // ── handlers ──────────────────────────────────────────────────────────────

  const handleTargetChange = (targetValue: string) => {
    const opt = targetOptions.find(o => o.value === targetValue)
    if (!opt) return
    const first = METRIC_CONFIGS[opt.scope][0]
    onChange({
      ...condition,
      scope:     opt.scope,
      leg_id:    opt.leg_id,
      metric:    first.value,
      timeframe: first.needsTimeframe ? (condition.timeframe ?? '15m') : null,
      params:    Object.fromEntries((first.params ?? []).map(p => [p.key, p.default])),
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

  const handleParam = (key: string, val: number) =>
    onChange({ ...condition, params: { ...condition.params, [key]: val } })

  // ── render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex items-stretch rounded-lg border border-border-default bg-surface-2 overflow-hidden text-xs group">

      {/* ── col: TARGET ─────────────────────────────────── */}
      <div className="px-3 py-2.5 border-r border-border-subtle min-w-[148px] flex-shrink-0">
        <div className={colLabelCls}>Target</div>
        <select
          value={currentTargetValue}
          onChange={e => handleTargetChange(e.target.value)}
          className={selectCls}
        >
          {targetOptions.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>

      {/* ── col: METRIC + inline params ─────────────────── */}
      <div className="px-3 py-2.5 border-r border-border-subtle flex-1 min-w-[180px]">
        <div className={colLabelCls}>
          {condition.scope === 'INDICATOR' ? 'Indicator' : 'Metric'}
          {metricCfg?.description && (
            <span className="ml-1 align-middle" title={metricCfg.description}>
              <Info size={9} className="inline text-text-muted" />
            </span>
          )}
        </div>
        <select
          value={condition.metric}
          onChange={e => handleMetricChange(e.target.value)}
          className={cn(selectCls, metricCfg?.params.length ? 'mb-2' : '')}
        >
          {METRIC_CONFIGS[condition.scope].map(m => (
            <option key={m.value} value={m.value}>{m.label}</option>
          ))}
        </select>

        {/* Inline param inputs */}
        {metricCfg && metricCfg.params.length > 0 && (
          <div className="flex items-center gap-3 flex-wrap">
            {metricCfg.params.map(p => (
              <div key={p.key} className="flex items-center gap-1.5">
                <span className="text-[9px] text-text-muted font-medium uppercase">{p.label}</span>
                <input
                  type="number"
                  value={condition.params[p.key] ?? p.default}
                  onChange={e => handleParam(p.key, Number(e.target.value))}
                  min={p.min}
                  max={p.max}
                  step={p.step ?? 1}
                  className={paramCls}
                />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── col: TIMEFRAME (indicators only) ────────────── */}
      {metricCfg?.needsTimeframe && (
        <div className="px-3 py-2.5 border-r border-border-subtle w-24 flex-shrink-0">
          <div className={colLabelCls}>Timeframe</div>
          <select
            value={condition.timeframe ?? '15m'}
            onChange={e =>
              onChange({ ...condition, timeframe: e.target.value as Timeframe })
            }
            className={selectCls}
          >
            {TIMEFRAME_OPTIONS.map(tf => (
              <option key={tf.value} value={tf.value}>{tf.label}</option>
            ))}
          </select>
        </div>
      )}

      {/* ── col: OPERATOR ───────────────────────────────── */}
      <div className="px-3 py-2.5 border-r border-border-subtle w-36 flex-shrink-0">
        <div className={colLabelCls}>Condition</div>
        <select
          value={condition.operator}
          onChange={e =>
            onChange({ ...condition, operator: e.target.value as ConditionOperator })
          }
          className={selectCls}
        >
          {OPERATOR_OPTIONS.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>

      {/* ── col: VALUE ──────────────────────────────────── */}
      <div className="px-3 py-2.5 w-28 flex-shrink-0">
        <div className={colLabelCls}>
          Value{metricCfg?.unit ? ` (${metricCfg.unit})` : ''}
        </div>
        {isCross ? (
          <div className="flex items-center h-[30px]">
            <span className="text-[11px] text-accent-blue italic">crossing indicator</span>
          </div>
        ) : (
          <input
            type="number"
            value={condition.value ?? ''}
            onChange={e =>
              onChange({
                ...condition,
                value: e.target.value === '' ? null : Number(e.target.value),
              })
            }
            placeholder="0"
            className={numCls}
          />
        )}
      </div>

      {/* ── remove ──────────────────────────────────────── */}
      <div className="flex items-center px-2 border-l border-border-subtle">
        <button
          type="button"
          onClick={onRemove}
          className="p-1 text-text-muted hover:text-loss transition-colors rounded hover:bg-loss/10"
          title="Remove condition"
        >
          <X size={13} />
        </button>
      </div>
    </div>
  )
}

// ── Condition group block (recursive) ────────────────────────────────────────

const DEPTH_COLORS = [
  'border-border-strong',
  'border-accent-blue/50',
  'border-accent-purple/50',
]

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
  const borderColor = DEPTH_COLORS[Math.min(depth, 2)]

  const toggleOp = () =>
    onChange({ ...group, op: group.op === 'AND' ? 'OR' : 'AND' })

  // ── condition CRUD ─────────────────────────────────────────────────────────

  const updateCondition = (idx: number, c: Condition) => {
    const conditions = [...group.conditions]
    conditions[idx] = c
    onChange({ ...group, conditions })
  }

  const removeCondition = (idx: number) =>
    onChange({ ...group, conditions: group.conditions.filter((_, i) => i !== idx) })

  const addCondition = (scope: ConditionScope = 'STRATEGY') =>
    onChange({
      ...group,
      conditions: [...group.conditions, defaultCondition(scope)],
    })

  // ── sub-group CRUD ─────────────────────────────────────────────────────────

  const updateNestedGroup = (idx: number, g: ConditionGroup) => {
    const groups = [...group.groups]
    groups[idx] = g
    onChange({ ...group, groups })
  }

  const removeNestedGroup = (idx: number) =>
    onChange({ ...group, groups: group.groups.filter((_, i) => i !== idx) })

  const addNestedGroup = () =>
    onChange({ ...group, groups: [...group.groups, defaultGroup()] })

  // ── total items (to know when to show connectors) ──────────────────────────

  const totalItems = group.conditions.length + group.groups.length

  return (
    <div
      className={cn(
        'rounded-lg border-l-2 pl-3 pr-1 py-3 space-y-0',
        borderColor,
        depth > 0 ? 'bg-surface-3/20 mt-1' : ''
      )}
    >
      {/* sub-group header */}
      {depth > 0 && (
        <div className="flex items-center gap-2 mb-2 -mt-1">
          <span className="text-[9px] text-text-muted uppercase tracking-widest font-semibold">
            Sub-group
          </span>
          {onRemove && (
            <button
              type="button"
              onClick={onRemove}
              className="ml-auto p-0.5 text-text-muted hover:text-loss transition-colors"
              title="Remove group"
            >
              <Trash2 size={11} />
            </button>
          )}
        </div>
      )}

      {/* conditions */}
      {group.conditions.map((cond, i) => (
        <div key={cond.id}>
          {i > 0 && <AndOrConnector op={group.op} onClick={toggleOp} />}
          <ConditionCard
            condition={cond}
            positionLegs={positionLegs}
            onChange={c => updateCondition(i, c)}
            onRemove={() => removeCondition(i)}
          />
        </div>
      ))}

      {/* nested groups */}
      {group.groups.map((sub, i) => (
        <div key={sub.id}>
          {(group.conditions.length > 0 || i > 0) && (
            <AndOrConnector op={group.op} onClick={toggleOp} />
          )}
          <GroupBlock
            group={sub}
            positionLegs={positionLegs}
            onChange={g => updateNestedGroup(i, g)}
            onRemove={() => removeNestedGroup(i)}
            depth={depth + 1}
          />
        </div>
      ))}

      {/* empty state */}
      {totalItems === 0 && (
        <div className="text-xs text-text-muted py-2 italic">
          No conditions — click below to add one.
        </div>
      )}

      {/* add buttons */}
      <div className="flex items-center gap-4 pt-3 mt-1 border-t border-border-subtle">
        {/* Quick-add scope buttons */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[9px] text-text-muted uppercase tracking-widest font-semibold">
            Add:
          </span>
          {(
            [
              { scope: 'STRATEGY' as ConditionScope, label: 'Strategy condition' },
              { scope: 'LEG'      as ConditionScope, label: 'Leg condition' },
              { scope: 'SPOT'     as ConditionScope, label: 'Spot condition' },
              { scope: 'INDICATOR' as ConditionScope, label: 'Indicator' },
            ] as const
          ).map(({ scope, label }) => (
            <button
              key={scope}
              type="button"
              onClick={() => addCondition(scope)}
              className="flex items-center gap-1 text-[11px] text-text-muted hover:text-accent-blue border border-border-subtle hover:border-accent-blue/50 rounded px-2 py-0.5 transition-colors"
            >
              <Plus size={10} />
              {label}
            </button>
          ))}
          {depth < 2 && (
            <button
              type="button"
              onClick={addNestedGroup}
              className="flex items-center gap-1 text-[11px] text-text-muted hover:text-accent-purple border border-border-subtle hover:border-accent-purple/50 rounded px-2 py-0.5 transition-colors"
            >
              <Plus size={10} />
              Sub-group ( )
            </button>
          )}
        </div>

        {/* Show current group op badge */}
        {totalItems > 1 && (
          <button
            type="button"
            onClick={toggleOp}
            className={cn(
              'ml-auto flex items-center gap-1 text-[10px] font-bold px-2.5 py-0.5 rounded-full border transition-colors',
              group.op === 'AND'
                ? 'border-accent-blue/40 text-accent-blue bg-accent-blue/10 hover:bg-accent-blue/20'
                : 'border-accent-amber/40 text-accent-amber bg-accent-amber/10 hover:bg-accent-amber/20'
            )}
            title="Toggle group operator"
          >
            All using {group.op}
          </button>
        )}
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
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <span className={cn(colLabelCls, 'mb-0')}>Conditions</span>
            <span className="text-[10px] text-text-muted">
              (click{' '}
              <span className="font-bold text-accent-blue">AND</span>
              {' / '}
              <span className="font-bold text-accent-amber">OR</span>
              {' '}connector to toggle)
            </span>
          </div>
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
