import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Layers, ChevronRight } from 'lucide-react'
import { toast } from 'react-hot-toast'
import { cn } from '@/lib/utils'
import { alertRuleService } from '@/services/alertService'
import type { AlertTemplate } from '@/services/alertService'
import { useStrategyStore } from '@store/strategyStore'

const SCOPE_COLORS: Record<string, string> = {
  STRATEGY: 'bg-accent-blue/20 text-accent-blue',
  LEG:      'bg-accent-purple/20 text-accent-purple',
  SPOT:     'bg-profit/20 text-profit',
  INDICATOR:'bg-accent-amber/20 text-accent-amber',
}

interface TemplateCardProps {
  template: AlertTemplate
  onUse: (template: AlertTemplate) => void
}

function TemplateCard({ template, onUse }: TemplateCardProps) {
  return (
    <div className="bg-surface-2 border border-border-subtle rounded-lg p-4 flex flex-col gap-3 hover:border-border-strong transition-colors">
      <div>
        <h3 className="text-sm font-semibold text-text-primary">{template.name}</h3>
        <p className="text-xs text-text-muted mt-0.5">{template.description}</p>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] text-text-muted">{template.alert_count} alert{template.alert_count !== 1 ? 's' : ''}</span>
        {template.scopes.map(scope => (
          <span key={scope} className={cn('text-[10px] px-1.5 py-0.5 rounded font-medium', SCOPE_COLORS[scope] ?? 'bg-surface-3 text-text-muted')}>
            {scope}
          </span>
        ))}
      </div>

      <div className="space-y-1">
        {template.preview.map((line, i) => (
          <div key={i} className="flex items-center gap-1.5 text-[11px] text-text-secondary">
            <ChevronRight size={10} className="text-text-muted shrink-0" />
            {line}
          </div>
        ))}
      </div>

      <button
        onClick={() => onUse(template)}
        className="mt-auto w-full py-2 text-xs font-medium border border-accent-blue text-accent-blue rounded-md hover:bg-accent-blue/10 transition-colors"
      >
        Use this template
      </button>
    </div>
  )
}

interface UseTemplateModalProps {
  template: AlertTemplate
  onClose: () => void
}

function UseTemplateModal({ template, onClose }: UseTemplateModalProps) {
  const [selectedStrategyId, setSelectedStrategyId] = useState('')
  const strategies = useStrategyStore(s => s.strategies)
  const qc = useQueryClient()

  const createMut = useMutation({
    mutationFn: () => alertRuleService.createFromTemplate(template.template_id, selectedStrategyId),
    onSuccess: (data: { created: number }) => {
      qc.invalidateQueries({ queryKey: ['alert-rules'] })
      qc.invalidateQueries({ queryKey: ['alert-rules-all'] })
      qc.invalidateQueries({ queryKey: ['alert-stats'] })
      toast.success(`Created ${data.created} alert${data.created !== 1 ? 's' : ''}`)
      onClose()
    },
    onError: () => toast.error('Failed to create alerts from template'),
  })

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-surface-1 border border-border-default rounded-xl shadow-modal w-full max-w-sm p-5 flex flex-col gap-4">
        <div>
          <h3 className="text-sm font-semibold text-text-primary">{template.name}</h3>
          <p className="text-xs text-text-muted mt-0.5">Select a position to apply this template to</p>
        </div>

        <div>
          <label className="block text-[10px] text-text-muted mb-1 uppercase tracking-wide">Position</label>
          <select
            value={selectedStrategyId}
            onChange={e => setSelectedStrategyId(e.target.value)}
            className="w-full px-3 py-1.5 text-sm bg-surface-2 border border-border-default rounded text-text-primary focus:outline-none focus:border-accent-blue"
          >
            <option value="">Select position…</option>
            {strategies.map(s => (
              <option key={s.id} value={s.id}>
                {s.name} {s.underlyingSymbol ? `(${s.underlyingSymbol})` : ''}
              </option>
            ))}
          </select>
        </div>

        <div className="text-xs text-text-muted">
          This will create <strong className="text-text-primary">{template.alert_count} alert{template.alert_count !== 1 ? 's' : ''}</strong> for the selected position.
        </div>

        <div className="flex items-center justify-end gap-2 pt-1 border-t border-border-subtle">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-xs text-text-muted hover:text-text-primary border border-border-default rounded transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => createMut.mutate()}
            disabled={!selectedStrategyId || createMut.isPending}
            className="px-4 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {createMut.isPending ? 'Creating…' : 'Create Alerts'}
          </button>
        </div>
      </div>
    </div>
  )
}

export function AlertTemplates() {
  const [selectedTemplate, setSelectedTemplate] = useState<AlertTemplate | null>(null)

  const { data: templates = [], isLoading } = useQuery({
    queryKey: ['alert-templates'],
    queryFn: () => alertRuleService.getTemplates(),
    staleTime: 300_000,
  })

  return (
    <div className="flex flex-col gap-4 p-6">
      <div className="flex items-center gap-2 mb-2">
        <Layers size={15} className="text-accent-blue" />
        <span className="text-sm font-medium text-text-primary">Alert Templates</span>
        <span className="text-xs text-text-muted">— pre-built alerts for common strategies</span>
      </div>

      {isLoading ? (
        <div className="text-center py-12 text-xs text-text-muted">Loading templates…</div>
      ) : (
        <div className="grid grid-cols-3 gap-4">
          {templates.map(t => (
            <TemplateCard key={t.template_id} template={t} onUse={setSelectedTemplate} />
          ))}
          {/* Custom card */}
          <div className="bg-surface-2 border border-dashed border-border-default rounded-lg p-4 flex flex-col items-center justify-center gap-3 hover:border-border-strong transition-colors">
            <Layers size={24} className="text-text-muted opacity-40" />
            <span className="text-sm text-text-secondary">Custom</span>
            <p className="text-xs text-text-muted text-center">Build an alert from scratch using the rule builder</p>
          </div>
        </div>
      )}

      {selectedTemplate && (
        <UseTemplateModal
          template={selectedTemplate}
          onClose={() => setSelectedTemplate(null)}
        />
      )}
    </div>
  )
}
