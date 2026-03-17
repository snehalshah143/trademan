import { useState, useCallback } from 'react'
import { Plus, Trash2, ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { AlertRuleBuilderData, Condition, ConditionGroup, ConditionScope, ConditionOperator } from '@/types/alertRules'
import {
  METRIC_OPTIONS,
  OPERATOR_OPTIONS,
  CROSS_OPERATORS,
  defaultCondition,
  defaultGroup,
  buildPreviewText,
} from '@/types/alertRules'

interface Leg {
  leg_id: string
  symbol: string
  side: 'BUY' | 'SELL'
}

interface AlertRuleBuilderProps {
  strategyId: string
  positionLegs: Leg[]
  initialData: AlertRuleBuilderData
  onSave: (data: AlertRuleBuilderData) => void
  onCancel: () => void
  isSaving?: boolean
}

// ── Style helpers ─────────────────────────────────────────────────────────────

const selectCls = 'bg-surface-3 border border-border-default text-text-primary text-xs rounded px-2 py-1 focus:outline-none focus:border-accent-blue'
const inputCls  = 'bg-surface-3 border border-border-default text-text-primary text-xs rounded px-2 py-1 focus:outline-none focus:border-accent-blue'

// ── Condition Row ─────────────────────────────────────────────────────────────

interface ConditionRowProps {
  condition: Condition
  positionLegs: Leg[]
  onChange: (c: Condition) => void
  onRemove: () => void
}

function ConditionRow({ condition, positionLegs, onChange, onRemove }: ConditionRowProps) {
  const isCross  = CROSS_OPERATORS.has(condition.operator)
  const metrics  = METRIC_OPTIONS[condition.scope]

  const handleScope = (scope: ConditionScope) => {
    onChange({
      ...condition,
      scope,
      metric:  METRIC_OPTIONS[scope][0].value,
      leg_id:  null,
    })
  }

  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {/* Scope */}
      <select
        value={condition.scope}
        onChange={e => handleScope(e.target.value as ConditionScope)}
        className={cn(selectCls, 'w-28')}
      >
        {(['STRATEGY', 'LEG', 'SPOT', 'INDICATOR'] as ConditionScope[]).map(s => (
          <option key={s} value={s}>{s}</option>
        ))}
      </select>

      {/* Leg selector (only for LEG scope) */}
      {condition.scope === 'LEG' && (
        <select
          value={condition.leg_id ?? ''}
          onChange={e => onChange({ ...condition, leg_id: e.target.value || null })}
          className={cn(selectCls, 'w-24')}
        >
          <option value="">Leg…</option>
          {positionLegs.map((l, i) => (
            <option key={l.leg_id} value={l.leg_id}>
              Leg {i + 1} ({l.side})
            </option>
          ))}
        </select>
      )}

      {/* Metric */}
      <select
        value={condition.metric}
        onChange={e => onChange({ ...condition, metric: e.target.value })}
        className={cn(selectCls, 'w-36')}
      >
        {metrics.map(m => (
          <option key={m.value} value={m.value}>{m.label}</option>
        ))}
      </select>

      {/* Operator */}
      <select
        value={condition.operator}
        onChange={e => onChange({ ...condition, operator: e.target.value as ConditionOperator })}
        className={cn(selectCls, 'w-28')}
      >
        {OPERATOR_OPTIONS.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>

      {/* Value (hidden for CROSS operators) */}
      {!isCross && (
        <input
          type="number"
          value={condition.value ?? ''}
          onChange={e => onChange({ ...condition, value: e.target.value === '' ? null : Number(e.target.value) })}
          placeholder="Value"
          className={cn(inputCls, 'w-20 tabular-nums')}
        />
      )}

      {/* Indicator params (period) */}
      {condition.scope === 'INDICATOR' && (
        <input
          type="number"
          value={(condition.params.period as number) ?? 14}
          onChange={e => onChange({ ...condition, params: { ...condition.params, period: Number(e.target.value) } })}
          placeholder="Period"
          className={cn(inputCls, 'w-16')}
          title="Period"
        />
      )}

      {/* Remove */}
      <button
        onClick={onRemove}
        className="p-1 text-text-muted hover:text-loss transition-colors"
        title="Remove condition"
      >
        <Trash2 size={12} />
      </button>
    </div>
  )
}

// ── Group Component ────────────────────────────────────────────────────────────

interface GroupProps {
  group: ConditionGroup
  positionLegs: Leg[]
  onChange: (g: ConditionGroup) => void
  onRemove?: () => void
  depth?: number
}

function GroupBlock({ group, positionLegs, onChange, onRemove, depth = 0 }: GroupProps) {
  const updateCondition = (idx: number, c: Condition) => {
    const conditions = [...group.conditions]
    conditions[idx] = c
    onChange({ ...group, conditions })
  }

  const removeCondition = (idx: number) => {
    onChange({ ...group, conditions: group.conditions.filter((_, i) => i !== idx) })
  }

  const addCondition = () => {
    onChange({
      ...group,
      conditions: [...group.conditions, defaultCondition('STRATEGY')],
    })
  }

  const addNestedGroup = () => {
    onChange({
      ...group,
      groups: [...group.groups, defaultGroup()],
    })
  }

  const updateNestedGroup = (idx: number, g: ConditionGroup) => {
    const groups = [...group.groups]
    groups[idx] = g
    onChange({ ...group, groups })
  }

  const removeNestedGroup = (idx: number) => {
    onChange({ ...group, groups: group.groups.filter((_, i) => i !== idx) })
  }

  return (
    <div className={cn('border rounded-md p-3 space-y-2', depth === 0 ? 'border-border-default bg-surface-2' : 'border-accent-blue/30 bg-surface-3')}>
      {/* Group header */}
      <div className="flex items-center gap-2">
        {/* AND / OR toggle */}
        <div className="flex rounded overflow-hidden border border-border-default">
          {(['AND', 'OR'] as const).map(op => (
            <button
              key={op}
              onClick={() => onChange({ ...group, op })}
              className={cn(
                'px-2 py-0.5 text-xs font-medium transition-colors',
                group.op === op
                  ? 'bg-accent-blue text-white'
                  : 'bg-surface-3 text-text-muted hover:text-text-secondary'
              )}
            >
              {op}
            </button>
          ))}
        </div>
        <span className="text-xs text-text-muted">
          {depth === 0 ? 'Root group' : 'Nested group'}
        </span>
        {onRemove && (
          <button
            onClick={onRemove}
            className="ml-auto p-1 text-text-muted hover:text-loss transition-colors"
            title="Remove group"
          >
            <Trash2 size={12} />
          </button>
        )}
      </div>

      {/* Conditions */}
      {group.conditions.map((cond, i) => (
        <div key={cond.id}>
          <ConditionRow
            condition={cond}
            positionLegs={positionLegs}
            onChange={c => updateCondition(i, c)}
            onRemove={() => removeCondition(i)}
          />
          {/* AND/OR separator between items */}
          {(i < group.conditions.length - 1 || group.groups.length > 0) && (
            <div className="text-[10px] text-accent-blue font-semibold py-1 pl-1">
              {group.op}
            </div>
          )}
        </div>
      ))}

      {/* Nested groups */}
      {group.groups.map((sub, i) => (
        <div key={sub.id}>
          {(group.conditions.length > 0 || i > 0) && (
            <div className="text-[10px] text-accent-blue font-semibold py-1 pl-1">
              {group.op}
            </div>
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

      {/* Add buttons */}
      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={addCondition}
          className="flex items-center gap-1 text-[11px] text-text-muted hover:text-accent-blue transition-colors"
        >
          <Plus size={11} />
          Add condition
        </button>
        {depth < 2 && (
          <button
            onClick={addNestedGroup}
            className="flex items-center gap-1 text-[11px] text-text-muted hover:text-accent-blue transition-colors"
          >
            <Plus size={11} />
            Nested group
          </button>
        )}
      </div>
    </div>
  )
}

// ── Notification Toggle ────────────────────────────────────────────────────────

function NotifToggle({
  label, checked, onChange,
}: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
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

// ── Main Component ─────────────────────────────────────────────────────────────

export function AlertRuleBuilder({
  strategyId: _strategyId,
  positionLegs,
  initialData,
  onSave,
  onCancel,
  isSaving = false,
}: AlertRuleBuilderProps) {
  const [form, setForm] = useState<AlertRuleBuilderData>(initialData)
  const [previewOpen, setPreviewOpen] = useState(true)

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
    <div className="flex flex-col gap-4 p-4 overflow-y-auto custom-scroll max-h-[80vh]">

      {/* ── Row 1: Name + trigger settings ─────────────────────────────────── */}
      <div className="grid grid-cols-3 gap-3">
        <div className="col-span-1">
          <label className="block text-[10px] text-text-muted mb-1 uppercase tracking-wide">Alert Name</label>
          <input
            value={form.name}
            onChange={e => update({ name: e.target.value })}
            placeholder="e.g. Stop Loss Hit"
            className={cn(inputCls, 'w-full')}
          />
        </div>
        <div>
          <label className="block text-[10px] text-text-muted mb-1 uppercase tracking-wide">Trigger</label>
          <button
            onClick={() => update({ trigger_once: !form.trigger_once })}
            className={cn(
              'w-full px-2 py-1 text-xs rounded border transition-colors',
              form.trigger_once
                ? 'border-accent-amber bg-accent-amber/10 text-accent-amber'
                : 'border-border-default text-text-muted hover:border-border-strong'
            )}
          >
            {form.trigger_once ? 'Once only' : 'Every time'}
          </button>
        </div>
        <div>
          <label className="block text-[10px] text-text-muted mb-1 uppercase tracking-wide">Cooldown (sec)</label>
          <input
            type="number"
            min={0}
            value={form.cooldown_secs}
            onChange={e => update({ cooldown_secs: Math.max(0, Number(e.target.value)) })}
            className={cn(inputCls, 'w-full tabular-nums')}
          />
        </div>
      </div>

      {/* ── Condition Tree ──────────────────────────────────────────────────── */}
      <div>
        <div className="text-[10px] text-text-muted mb-2 uppercase tracking-wide font-medium">
          Condition Tree
        </div>
        <GroupBlock
          group={form.condition_tree}
          positionLegs={positionLegs}
          onChange={setTree}
          depth={0}
        />
      </div>

      {/* ── Notifications ───────────────────────────────────────────────────── */}
      <div>
        <div className="text-[10px] text-text-muted mb-2 uppercase tracking-wide font-medium">
          Notifications
        </div>
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
            className={cn(inputCls, 'w-full mt-2')}
          />
        )}
        {form.notify_webhook && (
          <input
            value={form.webhook_url}
            onChange={e => update({ webhook_url: e.target.value })}
            placeholder="Webhook URL"
            className={cn(inputCls, 'w-full mt-2')}
          />
        )}
      </div>

      {/* ── Live Preview ─────────────────────────────────────────────────────── */}
      <div>
        <button
          onClick={() => setPreviewOpen(p => !p)}
          className="flex items-center gap-1.5 text-[10px] text-text-muted uppercase tracking-wide mb-1 hover:text-text-secondary transition-colors"
        >
          Preview
          {previewOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
        </button>
        {previewOpen && (
          <pre className="bg-surface-0 border border-border-subtle rounded p-3 text-[11px] text-text-secondary font-mono whitespace-pre-wrap leading-5">
            {previewText}
          </pre>
        )}
      </div>

      {/* ── Footer ──────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-end gap-3 pt-1 border-t border-border-subtle">
        <button
          onClick={onCancel}
          className="px-4 py-1.5 text-xs text-text-muted hover:text-text-primary border border-border-default rounded-md transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={handleSave}
          disabled={!form.name.trim() || isSaving}
          className="px-4 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {isSaving ? 'Saving…' : 'Save Alert'}
        </button>
      </div>
    </div>
  )
}
