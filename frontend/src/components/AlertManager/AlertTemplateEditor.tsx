/**
 * AlertTemplateEditor — modal for creating / editing a user-defined alert template.
 *
 * Layout:
 *   ┌──────────────────────────────────────────────────────────┐
 *   │  Template name + description                             │
 *   │  ── Alerts in this template ─────────────────────────── │
 *   │  [Alert card] name  preview  [Edit] [Delete]            │
 *   │  [+ Add Alert]                                           │
 *   │  ── Footer ──────────────────────────────────────────── │
 *   │  [Cancel]                        [Save Template]         │
 *   └──────────────────────────────────────────────────────────┘
 *
 * Clicking "Add Alert" or the edit icon opens AlertRuleBuilder
 * in an overlay inside the same modal.
 */
import { useState } from 'react'
import { Plus, Trash2, Edit2, X, ChevronRight } from 'lucide-react'
import { AlertRuleBuilder } from './AlertRuleBuilder'
import type { AlertRuleBuilderData } from '@/types/alertRules'
import { defaultAlertRule, buildPreviewText } from '@/types/alertRules'
import type { AlertTemplateAlert } from '@/services/alertService'
import { cn } from '@/lib/utils'

// ── Internal alert definition (includes a local id for list management) ───────

interface AlertDef extends AlertTemplateAlert {
  _localId: string
}

function alertDefToBuilderData(a: AlertDef): AlertRuleBuilderData {
  return {
    strategy_id:      '',
    name:             a.name,
    description:      a.description ?? '',
    is_active:        a.is_active,
    trigger_once:     a.trigger_once,
    cooldown_secs:    a.cooldown_secs,
    notify_popup:     a.notify_popup,
    notify_telegram:  a.notify_telegram,
    notify_email:     a.notify_email,
    notify_webhook:   a.notify_webhook,
    notify_sound:     a.notify_sound,
    webhook_url:      a.webhook_url ?? '',
    telegram_chat_id: a.telegram_chat_id ?? '',
    condition_tree:   a.condition_tree as AlertRuleBuilderData['condition_tree'],
  }
}

function builderDataToAlertDef(d: AlertRuleBuilderData, localId: string): AlertDef {
  return {
    _localId:         localId,
    name:             d.name,
    description:      d.description || null,
    is_active:        d.is_active,
    trigger_once:     d.trigger_once,
    cooldown_secs:    d.cooldown_secs,
    notify_popup:     d.notify_popup,
    notify_telegram:  d.notify_telegram,
    notify_email:     d.notify_email,
    notify_webhook:   d.notify_webhook,
    notify_sound:     d.notify_sound,
    webhook_url:      d.webhook_url || null,
    telegram_chat_id: d.telegram_chat_id || null,
    condition_tree:   d.condition_tree,
  }
}

function newAlertDef(): AlertDef {
  const base = defaultAlertRule('')
  return {
    _localId:         crypto.randomUUID(),
    name:             base.name,
    description:      base.description || null,
    is_active:        base.is_active,
    trigger_once:     base.trigger_once,
    cooldown_secs:    base.cooldown_secs,
    notify_popup:     base.notify_popup,
    notify_telegram:  base.notify_telegram,
    notify_email:     base.notify_email,
    notify_webhook:   base.notify_webhook,
    notify_sound:     base.notify_sound,
    webhook_url:      null,
    telegram_chat_id: null,
    condition_tree:   base.condition_tree,
  }
}

// ── Shared style helpers ───────────────────────────────────────────────────────

const inputCls =
  'bg-surface-3 border border-border-default text-text-primary text-xs rounded px-2 py-1.5 ' +
  'focus:outline-none focus:border-accent-blue w-full'

const labelCls = 'text-[9px] text-text-muted uppercase tracking-widest font-semibold mb-1.5 block'

// ── Alert card (collapsed summary row) ────────────────────────────────────────

interface AlertCardProps {
  alert:    AlertDef
  index:    number
  onEdit:   () => void
  onDelete: () => void
}

function AlertCard({ alert, index, onEdit, onDelete }: AlertCardProps) {
  const preview = buildPreviewText(alert.condition_tree as AlertRuleBuilderData['condition_tree'])
  const notifyLabels = [
    alert.notify_popup     && 'Popup',
    alert.notify_sound     && 'Sound',
    alert.notify_telegram  && 'Telegram',
    alert.notify_email     && 'Email',
    alert.notify_webhook   && 'Webhook',
  ].filter(Boolean) as string[]

  return (
    <div className="flex items-start gap-3 px-4 py-3 bg-surface-2 border border-border-subtle rounded-lg group">
      {/* index badge */}
      <span className="shrink-0 w-5 h-5 flex items-center justify-center rounded-full bg-accent-blue/15 text-accent-blue text-[10px] font-bold mt-0.5">
        {index + 1}
      </span>

      {/* content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-text-primary truncate">{alert.name || 'Unnamed Alert'}</span>
          {alert.trigger_once && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent-amber/15 text-accent-amber font-semibold">ONCE</span>
          )}
          {alert.cooldown_secs > 0 && (
            <span className="text-[9px] text-text-muted">{alert.cooldown_secs}s cooldown</span>
          )}
        </div>
        <div className="flex items-center gap-1 mt-0.5 text-[11px] text-text-muted">
          <ChevronRight size={10} className="shrink-0" />
          <span className="truncate">{preview}</span>
        </div>
        {notifyLabels.length > 0 && (
          <div className="flex gap-1 mt-1">
            {notifyLabels.map(l => (
              <span key={l} className="text-[9px] px-1.5 py-0.5 rounded bg-surface-3 text-text-muted">{l}</span>
            ))}
          </div>
        )}
      </div>

      {/* actions */}
      <div className="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={onEdit}
          className="p-1.5 rounded hover:bg-accent-blue/10 text-text-muted hover:text-accent-blue transition-colors"
          title="Edit alert"
        >
          <Edit2 size={13} />
        </button>
        <button
          onClick={onDelete}
          className="p-1.5 rounded hover:bg-loss/10 text-text-muted hover:text-loss transition-colors"
          title="Remove alert"
        >
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  )
}

// ── Placeholder legs for the template condition builder ───────────────────────
//
// Two groups are shown in the dropdown:
//
//   "Positional · by order"
//     Leg 1 … Leg 4  → matched by BUY-first position when applying
//                       Works for cash stocks, equities, or any leg type
//
//   "F&O Roles · by instrument type"
//     Buy Call (CE), Sell Call (CE), Buy Put (PE), Sell Put (PE),
//     Buy Future, Sell Future
//     → matched by symbol suffix (CE / PE / FUT) when applying
//
// Instrument type is detected smartly from the symbol ticker:
//   NIFTY24MAY24000CE → CE,  CRUDEOILMAY26FUT → FUT,  RELIANCE → EQ

export const TEMPLATE_ROLE_SLOTS = [
  { leg_id: 'leg_buy_call',  side: 'BUY'  as const, optionType: 'CE',  displayLabel: 'Buy Call (CE)'  },
  { leg_id: 'leg_sell_call', side: 'SELL' as const, optionType: 'CE',  displayLabel: 'Sell Call (CE)' },
  { leg_id: 'leg_buy_put',   side: 'BUY'  as const, optionType: 'PE',  displayLabel: 'Buy Put (PE)'   },
  { leg_id: 'leg_sell_put',  side: 'SELL' as const, optionType: 'PE',  displayLabel: 'Sell Put (PE)'  },
  { leg_id: 'leg_buy_fut',   side: 'BUY'  as const, optionType: 'FUT', displayLabel: 'Buy Future'     },
  { leg_id: 'leg_sell_fut',  side: 'SELL' as const, optionType: 'FUT', displayLabel: 'Sell Future'    },
]

const _POSITIONAL_GROUP = 'Positional · by order'
const _ROLE_GROUP       = 'F&O Roles · by instrument type'

const PLACEHOLDER_LEGS = [
  // Positional slots — BUY-first sorted position at apply time
  { leg_id: 'leg_1', symbol: 'Leg 1', side: 'BUY'  as const, displayLabel: 'Leg 1', group: _POSITIONAL_GROUP },
  { leg_id: 'leg_2', symbol: 'Leg 2', side: 'SELL' as const, displayLabel: 'Leg 2', group: _POSITIONAL_GROUP },
  { leg_id: 'leg_3', symbol: 'Leg 3', side: 'BUY'  as const, displayLabel: 'Leg 3', group: _POSITIONAL_GROUP },
  { leg_id: 'leg_4', symbol: 'Leg 4', side: 'SELL' as const, displayLabel: 'Leg 4', group: _POSITIONAL_GROUP },
  // Role-based slots — matched by symbol suffix at apply time
  ...TEMPLATE_ROLE_SLOTS.map(s => ({
    leg_id: s.leg_id, symbol: s.displayLabel, side: s.side,
    optionType: s.optionType, displayLabel: s.displayLabel, group: _ROLE_GROUP,
  })),
]

// ── Main component ─────────────────────────────────────────────────────────────

interface AlertTemplateEditorProps {
  /** undefined = create new */
  templateId?:         string
  initialName?:        string
  initialDescription?: string
  initialAlerts?:      AlertTemplateAlert[]
  onSave:    (name: string, description: string, alerts: AlertTemplateAlert[]) => void
  onCancel:  () => void
  isSaving?: boolean
}

export function AlertTemplateEditor({
  initialName        = '',
  initialDescription = '',
  initialAlerts      = [],
  onSave,
  onCancel,
  isSaving = false,
}: AlertTemplateEditorProps) {
  const [name,        setName       ] = useState(initialName)
  const [description, setDescription] = useState(initialDescription)
  const [alerts,      setAlerts     ] = useState<AlertDef[]>(() =>
    initialAlerts.map(a => ({ ...a, _localId: crypto.randomUUID() }))
  )

  // Which alert is being edited in the sub-overlay (null = closed)
  const [editingAlert, setEditingAlert] = useState<AlertDef | null>(null)

  const handleAddAlert = () => {
    setEditingAlert(newAlertDef())
  }

  const handleEditAlert = (a: AlertDef) => {
    setEditingAlert(a)
  }

  const handleDeleteAlert = (localId: string) => {
    setAlerts(prev => prev.filter(a => a._localId !== localId))
  }

  const handleSaveAlert = (data: AlertRuleBuilderData) => {
    if (!editingAlert) return
    const updated = builderDataToAlertDef(data, editingAlert._localId)
    setAlerts(prev => {
      const idx = prev.findIndex(a => a._localId === editingAlert._localId)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = updated
        return next
      }
      return [...prev, updated]
    })
    setEditingAlert(null)
  }

  const handleSaveTemplate = () => {
    if (!name.trim()) return
    const payload: AlertTemplateAlert[] = alerts.map(({ _localId: _unused, ...rest }) => rest)
    onSave(name.trim(), description.trim(), payload)
  }

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-surface-1 border border-border-default rounded-xl shadow-modal w-full max-w-2xl flex flex-col max-h-[90vh]">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle">
          <h2 className="text-sm font-semibold text-text-primary">
            {initialName ? 'Edit Template' : 'New Alert Template'}
          </h2>
          <button onClick={onCancel} className="p-1 rounded hover:bg-surface-3 text-text-muted transition-colors">
            <X size={15} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto custom-scroll px-5 py-4 space-y-5">

          {/* Template metadata */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelCls}>Template Name *</label>
              <input
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="e.g. Iron Condor Safety Net"
                className={inputCls}
              />
            </div>
            <div>
              <label className={labelCls}>Description</label>
              <input
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="Brief description of what this template does"
                className={inputCls}
              />
            </div>
          </div>

          {/* Alerts section */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <span className="text-[10px] text-text-muted uppercase tracking-widest font-semibold">
                Alerts in this template ({alerts.length})
              </span>
            </div>

            {alerts.length === 0 ? (
              <div className="border border-dashed border-border-default rounded-lg py-8 flex flex-col items-center gap-2 text-text-muted">
                <span className="text-xs">No alerts yet</span>
                <span className="text-[11px]">Click "Add Alert" to configure the first alert for this template</span>
              </div>
            ) : (
              <div className="space-y-2">
                {alerts.map((a, i) => (
                  <AlertCard
                    key={a._localId}
                    alert={a}
                    index={i}
                    onEdit={() => handleEditAlert(a)}
                    onDelete={() => handleDeleteAlert(a._localId)}
                  />
                ))}
              </div>
            )}

            <button
              onClick={handleAddAlert}
              className={cn(
                'mt-3 w-full flex items-center justify-center gap-1.5 py-2 text-xs',
                'border border-dashed border-accent-blue/40 text-accent-blue rounded-lg',
                'hover:bg-accent-blue/5 transition-colors'
              )}
            >
              <Plus size={13} />
              Add Alert
            </button>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-border-subtle bg-surface-2/50">
          <span className="text-[11px] text-text-muted">
            {alerts.length} alert{alerts.length !== 1 ? 's' : ''} · will be applied when this template is used
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={onCancel}
              className="px-4 py-1.5 text-xs text-text-muted hover:text-text-primary border border-border-default rounded-md transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSaveTemplate}
              disabled={!name.trim() || isSaving}
              className="px-5 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {isSaving ? 'Saving…' : 'Save Template'}
            </button>
          </div>
        </div>
      </div>

      {/* Sub-overlay: alert rule builder */}
      {editingAlert && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center p-4" style={{ zIndex: 60 }}>
          <div className="bg-surface-1 border border-border-default rounded-xl shadow-modal w-full max-w-3xl flex flex-col max-h-[92vh]">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle">
              <h3 className="text-sm font-semibold text-text-primary">
                {alerts.some(a => a._localId === editingAlert._localId)
                  ? 'Edit Alert'
                  : 'New Alert'}
              </h3>
              <button
                onClick={() => setEditingAlert(null)}
                className="p-1 rounded hover:bg-surface-3 text-text-muted transition-colors"
              >
                <X size={15} />
              </button>
            </div>
            <AlertRuleBuilder
              strategyId=""
              positionLegs={PLACEHOLDER_LEGS}
              initialData={alertDefToBuilderData(editingAlert)}
              onSave={handleSaveAlert}
              onCancel={() => setEditingAlert(null)}
            />
          </div>
        </div>
      )}
    </div>
  )
}
