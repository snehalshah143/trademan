/**
 * AlertTemplates — full CRUD for user-defined alert templates.
 *
 * Features:
 *  - List all DB-backed templates
 *  - Create a new template (via AlertTemplateEditor modal)
 *  - Edit an existing template
 *  - Delete a template
 *  - Apply a template to a strategy (creates N AlertRuleBuilder rows)
 */
import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Layers, Plus, Edit2, Trash2, Play, ChevronRight, ArrowRight, AlertTriangle } from 'lucide-react'
import { toast } from 'react-hot-toast'
import { cn } from '@/lib/utils'
import { alertTemplateService } from '@/services/alertService'
import type { AlertTemplateDB, AlertTemplateAlert } from '@/services/alertService'
import { AlertTemplateEditor, TEMPLATE_ROLE_SLOTS } from './AlertTemplateEditor'
import { useStrategyStore } from '@store/strategyStore'
import { buildPreviewText } from '@/types/alertRules'
import type { ConditionGroup } from '@/types/alertRules'

// ── Helpers ────────────────────────────────────────────────────────────────────

/** Smart instrument-type detection from symbol ticker + explicit field. */
function inferInstrumentType(symbol: string, explicitType?: string): string {
  if (explicitType && explicitType !== 'EQ') return explicitType
  const s = symbol.toUpperCase()
  if (s.endsWith('CE')) return 'CE'
  if (s.endsWith('PE')) return 'PE'
  if (s.includes('FUT')) return 'FUT'
  return 'EQ'
}

/** Collect all template slot ids referenced in a condition tree. */
function collectUsedSlots(tree: unknown): Set<string> {
  const slots = new Set<string>()
  if (!tree || typeof tree !== 'object') return slots
  const node = tree as Record<string, unknown>
  for (const cond of (node.conditions as unknown[] ?? [])) {
    if (typeof cond === 'object' && cond !== null) {
      const c = cond as Record<string, unknown>
      if (typeof c.leg_id === 'string' && c.leg_id.startsWith('leg_')) {
        slots.add(c.leg_id)
      }
    }
  }
  for (const sub of (node.groups as unknown[] ?? [])) {
    for (const s of collectUsedSlots(sub)) slots.add(s)
  }
  return slots
}

// ── Apply-to-strategy modal ────────────────────────────────────────────────────

interface ApplyModalProps {
  template: AlertTemplateDB
  onClose:  () => void
}

function ApplyModal({ template, onClose }: ApplyModalProps) {
  const [selectedId, setSelectedId] = useState('')
  const strategies = useStrategyStore(s => s.strategies)
  const qc = useQueryClient()

  // Collect which leg slots are actually used across all alerts in this template
  const usedSlots = useMemo<Set<string>>(() => {
    const all = new Set<string>()
    for (const alert of template.alerts) {
      for (const s of collectUsedSlots(alert.condition_tree)) all.add(s)
    }
    return all
  }, [template.alerts])

  const hasLegConditions = usedSlots.size > 0

  // Match each used role slot to the strategy leg with the same side + instrumentType
  const selectedStrategy = useMemo(
    () => strategies.find(s => s.id === selectedId) ?? null,
    [strategies, selectedId]
  )

  type StratLeg = { id: string; side: string; legIndex: number; instrument: { symbol: string; instrumentType: string; strike?: number | null } }

  const { legOverrides, mapping } = useMemo(() => {
    if (!selectedStrategy) return { legOverrides: undefined, mapping: [] }
    const legs = selectedStrategy.legs as StratLeg[]
    // Sorted BUY-first → legIndex for positional slots
    const sorted = [...legs].sort((a, b) => {
      if (a.side !== b.side) return a.side === 'BUY' ? -1 : 1
      return a.legIndex - b.legIndex
    })
    const overrides: Record<string, string> = {}
    const rows: Array<{ slot: string; slotLabel: string; leg: StratLeg | undefined; group: string }> = []

    // ── Positional slots: leg_1 → sorted[0], leg_2 → sorted[1], …
    const POSITIONAL = [
      { leg_id: 'leg_1', displayLabel: 'Leg 1' },
      { leg_id: 'leg_2', displayLabel: 'Leg 2' },
      { leg_id: 'leg_3', displayLabel: 'Leg 3' },
      { leg_id: 'leg_4', displayLabel: 'Leg 4' },
    ]
    for (let i = 0; i < POSITIONAL.length; i++) {
      const slot = POSITIONAL[i]
      if (!usedSlots.has(slot.leg_id)) continue
      const leg = sorted[i]
      rows.push({ slot: slot.leg_id, slotLabel: slot.displayLabel, leg, group: 'Positional' })
      if (leg) overrides[slot.leg_id] = leg.id
    }

    // ── Role-based slots: matched by side + inferred instrument type from symbol
    for (const roleSlot of TEMPLATE_ROLE_SLOTS) {
      if (!usedSlots.has(roleSlot.leg_id)) continue
      const matched = [...legs]
        .sort((a, b) => a.legIndex - b.legIndex)
        .find(l =>
          l.side === roleSlot.side &&
          inferInstrumentType(l.instrument.symbol, l.instrument.instrumentType) === roleSlot.optionType
        )
      rows.push({ slot: roleSlot.leg_id, slotLabel: roleSlot.displayLabel, leg: matched, group: 'F&O Role' })
      if (matched) overrides[roleSlot.leg_id] = matched.id
    }

    return { legOverrides: Object.keys(overrides).length ? overrides : undefined, mapping: rows }
  }, [selectedStrategy, usedSlots])

  const applyMut = useMutation({
    mutationFn: () => alertTemplateService.apply(template.template_id, selectedId, legOverrides),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['alert-rules'] })
      qc.invalidateQueries({ queryKey: ['alert-rules-all'] })
      qc.invalidateQueries({ queryKey: ['alert-stats'] })
      toast.success(`Created ${data.created} alert${data.created !== 1 ? 's' : ''} from "${template.name}"`)
      onClose()
    },
    onError: () => toast.error('Failed to apply template'),
  })

  // Check for unmapped slots (template uses a slot but strategy has no leg for it)
  const unmapped = mapping.filter(r => !r.leg)

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-surface-1 border border-border-default rounded-xl shadow-modal w-full max-w-md p-5 flex flex-col gap-4">
        <div>
          <h3 className="text-sm font-semibold text-text-primary">Apply Template</h3>
          <p className="text-xs text-text-muted mt-0.5">
            "{template.name}" · {template.alerts.length} alert{template.alerts.length !== 1 ? 's' : ''}
          </p>
        </div>

        <div>
          <label className="block text-[10px] text-text-muted mb-1 uppercase tracking-wide">Apply to strategy</label>
          <select
            value={selectedId}
            onChange={e => setSelectedId(e.target.value)}
            className="w-full px-3 py-1.5 text-sm bg-surface-2 border border-border-default rounded text-text-primary focus:outline-none focus:border-accent-blue"
          >
            <option value="">Select strategy…</option>
            {strategies.map(s => (
              <option key={s.id} value={s.id}>
                {s.name} {s.underlyingSymbol ? `(${s.underlyingSymbol})` : ''}
              </option>
            ))}
          </select>
        </div>

        {/* Leg mapping preview — only shown when template has LEG-scoped conditions */}
        {hasLegConditions && selectedStrategy && mapping.length > 0 && (
          <div className="rounded-lg border border-border-subtle overflow-hidden">
            <div className="px-3 py-2 bg-surface-3 border-b border-border-subtle">
              <span className="text-[10px] text-text-muted uppercase tracking-widest font-semibold">
                Leg slot mapping
              </span>
            </div>
            <div className="divide-y divide-border-subtle">
              {mapping.map(({ slot, slotLabel, leg, group }) => (
                <div key={slot} className="flex items-center gap-2 px-3 py-2">
                  {/* Group badge */}
                  <span className={cn(
                    'text-[9px] font-semibold px-1.5 py-0.5 rounded shrink-0',
                    group === 'Positional'
                      ? 'bg-accent-blue/10 text-accent-blue'
                      : 'bg-accent-purple/10 text-accent-purple'
                  )}>
                    {group === 'Positional' ? 'POS' : 'F&O'}
                  </span>
                  <span className={cn(
                    'text-[11px] font-semibold w-28 shrink-0',
                    slotLabel.startsWith('Buy') || slotLabel === 'Leg 1' || slotLabel === 'Leg 3'
                      ? 'text-text-primary'
                      : 'text-text-primary'
                  )}>
                    {slotLabel}
                  </span>
                  <ArrowRight size={11} className="text-text-muted shrink-0" />
                  {leg ? (
                    <span className="text-[11px] text-text-primary">
                      {leg.instrument.strike != null ? `${leg.instrument.strike} ` : ''}
                      <span className="font-medium">
                        {inferInstrumentType(leg.instrument.symbol, leg.instrument.instrumentType)}
                      </span>
                      <span className={cn('ml-1 text-[10px]', leg.side === 'BUY' ? 'text-profit' : 'text-loss')}>
                        ({leg.side})
                      </span>
                      <span className="ml-1 text-[10px] text-text-muted">{leg.instrument.symbol}</span>
                    </span>
                  ) : (
                    <span className="text-[11px] text-accent-amber flex items-center gap-1">
                      <AlertTriangle size={10} />
                      No matching leg in this strategy
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {unmapped.length > 0 && (
          <p className="text-xs text-accent-amber flex items-center gap-1.5">
            <AlertTriangle size={12} />
            {unmapped.length} role{unmapped.length > 1 ? 's' : ''} not found in this strategy — conditions for those legs will not fire.
          </p>
        )}

        {!hasLegConditions && (
          <p className="text-xs text-text-muted">
            This template has no leg-specific conditions — it will apply identically to all strategies.
          </p>
        )}

        <div className="flex items-center justify-end gap-2 pt-1 border-t border-border-subtle">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-xs text-text-muted hover:text-text-primary border border-border-default rounded transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => applyMut.mutate()}
            disabled={!selectedId || applyMut.isPending}
            className="px-4 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {applyMut.isPending ? 'Applying…' : 'Apply Template'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Template card ──────────────────────────────────────────────────────────────

interface TemplateCardProps {
  template:  AlertTemplateDB
  onEdit:    () => void
  onDelete:  () => void
  onApply:   () => void
}

function TemplateCard({ template, onEdit, onDelete, onApply }: TemplateCardProps) {
  // Show first 2 alert names as preview
  const alertPreviews = template.alerts.slice(0, 3)

  return (
    <div className="bg-surface-2 border border-border-subtle rounded-lg p-4 flex flex-col gap-3 hover:border-border-strong transition-colors group">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-text-primary truncate">{template.name}</h3>
          {template.description && (
            <p className="text-xs text-text-muted mt-0.5 line-clamp-2">{template.description}</p>
          )}
        </div>
        {/* Action buttons — visible on hover */}
        <div className="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={onEdit}
            title="Edit template"
            className="p-1.5 rounded hover:bg-accent-blue/10 text-text-muted hover:text-accent-blue transition-colors"
          >
            <Edit2 size={13} />
          </button>
          <button
            onClick={onDelete}
            title="Delete template"
            className="p-1.5 rounded hover:bg-loss/10 text-text-muted hover:text-loss transition-colors"
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      {/* Alert previews */}
      <div className="space-y-1.5 flex-1">
        {alertPreviews.length === 0 ? (
          <span className="text-[11px] text-text-muted italic">No alerts configured</span>
        ) : (
          alertPreviews.map((a, i) => {
            const preview = buildPreviewText(a.condition_tree as ConditionGroup)
            return (
              <div key={i} className="flex items-start gap-1.5 text-[11px] text-text-secondary">
                <ChevronRight size={10} className="text-text-muted shrink-0 mt-0.5" />
                <div className="min-w-0">
                  <span className="font-medium text-text-primary">{a.name || 'Alert'}</span>
                  {preview && preview !== '(no conditions)' && (
                    <span className="text-text-muted"> — {preview.slice(0, 60)}{preview.length > 60 ? '…' : ''}</span>
                  )}
                </div>
              </div>
            )
          })
        )}
        {template.alerts.length > 3 && (
          <p className="text-[10px] text-text-muted pl-3.5">+ {template.alerts.length - 3} more alert{template.alerts.length - 3 !== 1 ? 's' : ''}</p>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between pt-2 border-t border-border-subtle">
        <span className="text-[10px] text-text-muted">
          {template.alerts.length} alert{template.alerts.length !== 1 ? 's' : ''}
        </span>
        <button
          onClick={onApply}
          disabled={template.alerts.length === 0}
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-colors',
            template.alerts.length > 0
              ? 'border border-accent-blue text-accent-blue hover:bg-accent-blue/10'
              : 'border border-border-default text-text-muted cursor-not-allowed opacity-50'
          )}
        >
          <Play size={11} />
          Apply to Strategy
        </button>
      </div>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export function AlertTemplates() {
  const qc = useQueryClient()

  const [creatingNew,     setCreatingNew    ] = useState(false)
  const [editingTemplate, setEditingTemplate] = useState<AlertTemplateDB | null>(null)
  const [applyingTemplate, setApplyingTemplate] = useState<AlertTemplateDB | null>(null)

  const { data: templates = [], isLoading } = useQuery({
    queryKey: ['alert-templates-db'],
    queryFn:  () => alertTemplateService.list(),
    staleTime: 60_000,
  })

  const createMut = useMutation({
    mutationFn: (data: { name: string; description: string; alerts: AlertTemplateAlert[] }) =>
      alertTemplateService.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alert-templates-db'] })
      toast.success('Template created')
      setCreatingNew(false)
    },
    onError: () => toast.error('Failed to create template'),
  })

  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: string; data: { name: string; description: string; alerts: AlertTemplateAlert[] } }) =>
      alertTemplateService.update(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alert-templates-db'] })
      toast.success('Template updated')
      setEditingTemplate(null)
    },
    onError: () => toast.error('Failed to update template'),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => alertTemplateService.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alert-templates-db'] })
      toast.success('Template deleted')
    },
    onError: () => toast.error('Failed to delete template'),
  })

  const handleCreateSave = (name: string, description: string, alerts: AlertTemplateAlert[]) => {
    createMut.mutate({ name, description, alerts })
  }

  const handleEditSave = (name: string, description: string, alerts: AlertTemplateAlert[]) => {
    if (!editingTemplate) return
    updateMut.mutate({ id: editingTemplate.template_id, data: { name, description, alerts } })
  }

  const handleDelete = (t: AlertTemplateDB) => {
    if (!window.confirm(`Delete template "${t.name}"? This cannot be undone.`)) return
    deleteMut.mutate(t.template_id)
  }

  return (
    <div className="flex flex-col gap-4 p-6">

      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Layers size={15} className="text-accent-blue" />
          <span className="text-sm font-medium text-text-primary">Alert Templates</span>
          <span className="text-xs text-text-muted">— reusable alert bundles, apply to any strategy in one click</span>
        </div>
        <button
          onClick={() => setCreatingNew(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
        >
          <Plus size={13} />
          New Template
        </button>
      </div>

      {/* Template grid */}
      {isLoading ? (
        <div className="text-center py-12 text-xs text-text-muted">Loading templates…</div>
      ) : templates.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 gap-3 text-text-muted">
          <Layers size={32} className="opacity-20" />
          <span className="text-sm">No templates yet</span>
          <p className="text-xs text-center max-w-xs">
            Create a template with multiple pre-configured alert conditions.
            Apply the whole template to a strategy with one click.
          </p>
          <button
            onClick={() => setCreatingNew(true)}
            className="flex items-center gap-1.5 px-4 py-2 text-xs font-medium border border-accent-blue text-accent-blue rounded-md hover:bg-accent-blue/10 transition-colors mt-1"
          >
            <Plus size={13} />
            Create First Template
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-4">
          {templates.map(t => (
            <TemplateCard
              key={t.template_id}
              template={t}
              onEdit={() => setEditingTemplate(t)}
              onDelete={() => handleDelete(t)}
              onApply={() => setApplyingTemplate(t)}
            />
          ))}
        </div>
      )}

      {/* Create modal */}
      {creatingNew && (
        <AlertTemplateEditor
          onSave={handleCreateSave}
          onCancel={() => setCreatingNew(false)}
          isSaving={createMut.isPending}
        />
      )}

      {/* Edit modal */}
      {editingTemplate && (
        <AlertTemplateEditor
          templateId={editingTemplate.template_id}
          initialName={editingTemplate.name}
          initialDescription={editingTemplate.description ?? ''}
          initialAlerts={editingTemplate.alerts}
          onSave={handleEditSave}
          onCancel={() => setEditingTemplate(null)}
          isSaving={updateMut.isPending}
        />
      )}

      {/* Apply modal */}
      {applyingTemplate && (
        <ApplyModal
          template={applyingTemplate}
          onClose={() => setApplyingTemplate(null)}
        />
      )}
    </div>
  )
}
