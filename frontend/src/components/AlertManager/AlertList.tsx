import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Edit2, Trash2, ToggleLeft, ToggleRight, Bell } from 'lucide-react'
import * as Dialog from '@radix-ui/react-dialog'
import { toast } from 'react-hot-toast'
import { cn } from '@/lib/utils'
import { alertRuleService } from '@/services/alertService'
import { AlertRuleBuilder } from './AlertRuleBuilder'
import { defaultAlertRule, buildPreviewText } from '@/types/alertRules'
import type { AlertRuleBuilderData } from '@/types/alertRules'

interface Leg {
  leg_id: string
  symbol: string
  side: 'BUY' | 'SELL'
}

interface AlertListProps {
  strategyId: string
  strategyName?: string
  positionLegs: Leg[]
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function AlertList({ strategyId, strategyName, positionLegs }: AlertListProps) {
  const queryClient = useQueryClient()
  const [builderOpen, setBuilderOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<AlertRuleBuilderData | null>(null)

  const qKey = ['alert-rules', strategyId]

  const { data: rules = [], isLoading } = useQuery({
    queryKey: qKey,
    queryFn: () => alertRuleService.list(strategyId),
    staleTime: 10_000,
  })

  const toggleMutation = useMutation({
    mutationFn: (alertId: string) => alertRuleService.toggle(alertId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qKey }),
    onError: () => toast.error('Failed to toggle alert'),
  })

  const deleteMutation = useMutation({
    mutationFn: (alertId: string) => alertRuleService.delete(alertId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qKey })
      toast.success('Alert deleted')
    },
    onError: () => toast.error('Failed to delete alert'),
  })

  const saveMutation = useMutation({
    mutationFn: (data: AlertRuleBuilderData) => {
      if (data.alert_id) {
        return alertRuleService.update(data.alert_id, data)
      }
      return alertRuleService.create(data)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qKey })
      toast.success('Alert saved')
      setBuilderOpen(false)
      setEditTarget(null)
    },
    onError: () => toast.error('Failed to save alert'),
  })

  const openCreate = () => {
    setEditTarget(null)
    setBuilderOpen(true)
  }

  const openEdit = (rule: AlertRuleBuilderData) => {
    setEditTarget(rule)
    setBuilderOpen(true)
  }

  const handleSave = (data: AlertRuleBuilderData) => {
    saveMutation.mutate(data)
  }

  const handleDelete = (alertId: string) => {
    if (!confirm('Delete this alert rule?')) return
    deleteMutation.mutate(alertId)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle shrink-0">
        <div className="flex items-center gap-2">
          <Bell size={14} className="text-accent-amber" />
          <span className="text-sm font-medium text-text-primary">Alert Rules</span>
          {strategyName && (
            <span className="text-xs text-text-muted">{strategyName}</span>
          )}
        </div>
        <button
          onClick={openCreate}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
        >
          <Plus size={12} />
          New Alert
        </button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto custom-scroll">
        {isLoading ? (
          <div className="flex items-center justify-center py-12 text-text-muted text-xs">
            Loading…
          </div>
        ) : rules.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center px-6">
            <Bell size={28} className="text-text-muted opacity-30 mb-3" />
            <p className="text-sm text-text-secondary">No alert rules yet</p>
            <p className="text-xs text-text-muted mt-1">
              Create rules to get notified when conditions are met
            </p>
            <button
              onClick={openCreate}
              className="mt-4 px-4 py-2 text-xs font-medium border border-accent-blue text-accent-blue rounded-md hover:bg-accent-blue/10 transition-colors"
            >
              Create first alert
            </button>
          </div>
        ) : (
          <div className="divide-y divide-border-subtle">
            {rules.map(rule => (
              <div
                key={rule.alert_id}
                className="px-4 py-3 hover:bg-surface-2 transition-colors"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={cn(
                        'text-sm font-medium',
                        rule.is_active ? 'text-text-primary' : 'text-text-muted line-through'
                      )}>
                        {rule.name}
                      </span>
                      {(rule.triggered_count ?? 0) > 0 && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent-amber/10 text-accent-amber border border-accent-amber/20">
                          ×{rule.triggered_count}
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-text-muted font-mono truncate">
                      {buildPreviewText(rule.condition_tree).split('\n')[0]}
                    </p>
                    {rule.last_triggered && (
                      <p className="text-[10px] text-text-muted mt-1">
                        Last fired: {fmtDate(rule.last_triggered)}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={() => toggleMutation.mutate(rule.alert_id!)}
                      className="p-1 text-text-muted hover:text-accent-blue transition-colors"
                      title={rule.is_active ? 'Disable' : 'Enable'}
                    >
                      {rule.is_active
                        ? <ToggleRight size={14} className="text-accent-blue" />
                        : <ToggleLeft  size={14} />
                      }
                    </button>
                    <button
                      onClick={() => openEdit(rule)}
                      className="p-1 text-text-muted hover:text-text-primary transition-colors"
                      title="Edit"
                    >
                      <Edit2 size={13} />
                    </button>
                    <button
                      onClick={() => handleDelete(rule.alert_id!)}
                      className="p-1 text-text-muted hover:text-loss transition-colors"
                      title="Delete"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Builder Dialog */}
      <Dialog.Root open={builderOpen} onOpenChange={open => { setBuilderOpen(open); if (!open) setEditTarget(null) }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[640px] max-w-[95vw] bg-surface-1 border border-border-default rounded-xl shadow-modal overflow-hidden">
            <Dialog.Title className="sr-only">
              {editTarget ? 'Edit Alert Rule' : 'New Alert Rule'}
            </Dialog.Title>
            <div className="px-4 py-3 border-b border-border-subtle">
              <span className="text-sm font-semibold text-text-primary">
                {editTarget ? 'Edit Alert Rule' : 'New Alert Rule'}
              </span>
            </div>
            <AlertRuleBuilder
              strategyId={strategyId}
              positionLegs={positionLegs}
              initialData={editTarget ?? defaultAlertRule(strategyId)}
              onSave={handleSave}
              onCancel={() => { setBuilderOpen(false); setEditTarget(null) }}
              isSaving={saveMutation.isPending}
            />
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  )
}
