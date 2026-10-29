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
import axios from 'axios'
import { cn } from '@/lib/utils'
import { alertTemplateService } from '@/services/alertService'
import type { AlertTemplateDB, AlertTemplateAlert } from '@/services/alertService'
import { AlertTemplateEditor, TEMPLATE_ROLE_SLOTS } from './AlertTemplateEditor'
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

// Shape returned by GET /api/v1/strategies and /api/v1/strategies/{id}
interface BackendLeg {
  id:          string
  symbol:      string
  action:      string         // 'BUY' | 'SELL'
  option_type: string | null  // 'CE' | 'PE' | 'FUT' | null
  strike:      number | null
}
interface BackendStrategy {
  id:   string
  name: string
  legs: BackendLeg[]
}

function ApplyModal({ template, onClose }: ApplyModalProps) {
  const [selectedId, setSelectedId] = useState('')
  const [manualMap,  setManualMap]  = useState<Record<string, string>>({})
  const qc = useQueryClient()

  // ── Strategy list from backend (always current, not stale store) ─────────────
  const { data: backendStrategies = [] } = useQuery<BackendStrategy[]>({
    queryKey: ['strategies-for-apply-modal'],
    queryFn:  () => axios.get('/api/v1/strategies').then(r => r.data),
    staleTime: 30_000,
  })

  // ── Fetch selected strategy's legs from backend (authoritative) ──────────────
  const { data: selectedDetail, isFetching: legsFetching } = useQuery<BackendStrategy>({
    queryKey: ['strategy-detail-apply', selectedId],
    queryFn:  () => axios.get(`/api/v1/strategies/${selectedId}`).then(r => r.data),
    enabled:  !!selectedId,
    staleTime: 10_000,
  })

  // Normalise backend legs: action→side, option_type→instrumentType
  type NormLeg = { id: string; side: string; instrumentType: string; symbol: string; strike: number | null }
  const normLegs = useMemo<NormLeg[]>(() => {
    if (!selectedDetail) return []
    return selectedDetail.legs.map(l => ({
      id:             String(l.id),
      side:           l.action,
      instrumentType: l.option_type ?? inferInstrumentType(l.symbol, undefined),
      symbol:         l.symbol,
      strike:         l.strike,
    }))
  }, [selectedDetail])

  // ── Collect which slots are used across all alerts in this template ───────────
  const usedSlots = useMemo<Set<string>>(() => {
    const all = new Set<string>()
    for (const alert of template.alerts) {
      for (const s of collectUsedSlots(alert.condition_tree)) all.add(s)
    }
    return all
  }, [template.alerts])

  const hasLegConditions = usedSlots.size > 0

  // Which alerts reference each slot (shown in the mapping table)
  const slotAlertNames = useMemo<Record<string, string[]>>(() => {
    const map: Record<string, string[]> = {}
    for (const alert of template.alerts) {
      for (const slot of collectUsedSlots(alert.condition_tree)) {
        if (!map[slot]) map[slot] = []
        if (!map[slot].includes(alert.name)) map[slot].push(alert.name)
      }
    }
    return map
  }, [template.alerts])

  // ── Auto-detect initial mapping ───────────────────────────────────────────────
  const autoMapping = useMemo<Record<string, string>>(() => {
    if (!normLegs.length) return {}
    const result: Record<string, string> = {}

    // Positional: BUY-first sort
    const sorted = [...normLegs].sort((a, b) =>
      a.side !== b.side ? (a.side === 'BUY' ? -1 : 1) : 0
    )
    const POSITIONAL = ['leg_1', 'leg_2', 'leg_3', 'leg_4']
    POSITIONAL.forEach((slot, i) => {
      if (usedSlots.has(slot) && sorted[i]) result[slot] = sorted[i].id
    })

    // Role-based: match by side + instrument type
    for (const r of TEMPLATE_ROLE_SLOTS) {
      if (!usedSlots.has(r.leg_id)) continue
      const matched = normLegs.find(l => l.side === r.side && l.instrumentType === r.optionType)
      if (matched) result[r.leg_id] = matched.id
    }
    return result
  }, [normLegs, usedSlots])

  // Reset manual overrides when strategy changes
  const handleStrategyChange = (id: string) => {
    setSelectedId(id)
    setManualMap({})
  }

  const effectiveMap = useMemo(() => ({ ...autoMapping, ...manualMap }), [autoMapping, manualMap])

  // Rows to show in the mapping table
  const mappingRows = useMemo(() => {
    if (!selectedId) return []
    const rows: Array<{ slot: string; slotLabel: string; group: string }> = []
    const POSITIONAL = [
      { leg_id: 'leg_1', displayLabel: 'Leg 1' },
      { leg_id: 'leg_2', displayLabel: 'Leg 2' },
      { leg_id: 'leg_3', displayLabel: 'Leg 3' },
      { leg_id: 'leg_4', displayLabel: 'Leg 4' },
    ]
    for (const p of POSITIONAL) {
      if (usedSlots.has(p.leg_id)) rows.push({ slot: p.leg_id, slotLabel: p.displayLabel, group: 'Positional' })
    }
    for (const r of TEMPLATE_ROLE_SLOTS) {
      if (usedSlots.has(r.leg_id)) rows.push({ slot: r.leg_id, slotLabel: r.displayLabel, group: 'F&O Role' })
    }
    return rows
  }, [selectedId, usedSlots])

  const applyMut = useMutation({
    mutationFn: () => {
      const overrides = Object.keys(effectiveMap).length ? effectiveMap : undefined
      return alertTemplateService.apply(template.template_id, selectedId, overrides)
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['alert-rules'] })
      qc.invalidateQueries({ queryKey: ['alert-rules-all'] })
      qc.invalidateQueries({ queryKey: ['alert-stats'] })
      toast.success(`Created ${data.created} alert${data.created !== 1 ? 's' : ''} from "${template.name}"`)
      onClose()
    },
    onError: () => toast.error('Failed to apply template'),
  })

  const unmappedCount = hasLegConditions
    ? mappingRows.filter(r => !effectiveMap[r.slot]).length
    : 0

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-surface-1 border border-border-default rounded-xl shadow-modal w-full max-w-lg p-5 flex flex-col gap-4">
        <div>
          <h3 className="text-sm font-semibold text-text-primary">Apply Template</h3>
          <p className="text-xs text-text-muted mt-0.5">
            "{template.name}" · {template.alerts.length} alert{template.alerts.length !== 1 ? 's' : ''}
          </p>
        </div>

        {/* Strategy selector */}
        <div>
          <label className="block text-[10px] text-text-muted mb-1 uppercase tracking-wide">Apply to strategy</label>
          <select
            value={selectedId}
            onChange={e => handleStrategyChange(e.target.value)}
            className="w-full px-3 py-1.5 text-sm bg-surface-2 border border-border-default rounded text-text-primary focus:outline-none focus:border-accent-blue"
          >
            <option value="">Select strategy…</option>
            {backendStrategies.map(s => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        </div>

        {/* Leg mapping table */}
        {hasLegConditions && selectedId && (
          <div className="rounded-lg border border-border-subtle overflow-hidden">
            <div className="px-3 py-2 bg-surface-3 border-b border-border-subtle flex items-center gap-2">
              <span className="text-[10px] text-text-muted uppercase tracking-widest font-semibold">
                Leg mapping
              </span>
              {legsFetching && <span className="text-[10px] text-text-muted">Loading…</span>}
            </div>

            {normLegs.length === 0 && !legsFetching ? (
              <div className="px-3 py-3 text-xs text-text-muted">No legs found for this strategy.</div>
            ) : (
              <div className="divide-y divide-border-subtle">
                {mappingRows.map(({ slot, slotLabel, group }) => {
                  const assignedId   = effectiveMap[slot] ?? ''
                  const isAutoMapped = !!autoMapping[slot] && !manualMap[slot]
                  const usedByAlerts = slotAlertNames[slot] ?? []

                  return (
                    <div key={slot} className="px-3 py-2.5 flex flex-col gap-1.5">
                      {/* Slot header */}
                      <div className="flex items-center gap-2">
                        <span className={cn(
                          'text-[9px] font-semibold px-1.5 py-0.5 rounded shrink-0',
                          group === 'Positional'
                            ? 'bg-accent-blue/10 text-accent-blue'
                            : 'bg-accent-purple/10 text-accent-purple'
                        )}>
                          {group === 'Positional' ? 'POS' : 'F&O'}
                        </span>
                        <span className="text-[11px] font-semibold text-text-primary">{slotLabel}</span>
                        {usedByAlerts.length > 0 && (
                          <span className="text-[10px] text-text-muted truncate">
                            — used by: {usedByAlerts.join(', ')}
                          </span>
                        )}
                      </div>
                      {/* Dropdown */}
                      <div className="flex items-center gap-2 pl-1">
                        <ArrowRight size={11} className="text-text-muted shrink-0" />
                        <select
                          value={assignedId}
                          onChange={e => setManualMap(prev => ({ ...prev, [slot]: e.target.value }))}
                          className={cn(
                            'flex-1 px-2 py-1 text-[11px] rounded border bg-surface-2 text-text-primary focus:outline-none focus:border-accent-blue',
                            assignedId
                              ? isAutoMapped ? 'border-border-subtle' : 'border-accent-blue'
                              : 'border-accent-amber text-accent-amber'
                          )}
                        >
                          <option value="">— not mapped —</option>
                          {normLegs.map(l => (
                            <option key={l.id} value={l.id}>
                              {l.side} {l.instrumentType}{l.strike != null ? ` ${l.strike}` : ''} · {l.symbol}
                            </option>
                          ))}
                        </select>
                        {isAutoMapped && assignedId && (
                          <span className="text-[9px] text-profit shrink-0">auto ✓</span>
                        )}
                        {!assignedId && (
                          <span className="text-[9px] text-accent-amber shrink-0">unset</span>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {unmappedCount > 0 && (
          <p className="text-xs text-accent-amber flex items-center gap-1.5">
            <AlertTriangle size={12} />
            {unmappedCount} slot{unmappedCount > 1 ? 's' : ''} unmapped — those leg conditions will not fire.
          </p>
        )}

        {!hasLegConditions && (
          <p className="text-xs text-text-muted">
            No leg-specific conditions — applies identically to any strategy.
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
            disabled={!selectedId || applyMut.isPending || legsFetching}
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
