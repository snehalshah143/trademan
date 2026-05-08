import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, ToggleLeft, ToggleRight, Edit2, Trash2, RotateCcw, ChevronDown, ChevronRight, Search, Check } from 'lucide-react'
import * as Dialog from '@radix-ui/react-dialog'
import { toast } from 'react-hot-toast'
import { cn } from '@/lib/utils'
import { alertRuleService } from '@/services/alertService'
import { AlertList } from './AlertList'
import { buildPreviewText } from '@/types/alertRules'
import type { AlertRuleBuilderData } from '@/types/alertRules'
import { useStrategyStore } from '@store/strategyStore'

// ── Persist custom display names for alert groups (survives store resets) ─────

const NAMES_KEY = 'trademan-alert-group-names'

function useGroupNames() {
  const [names, setNamesState] = useState<Record<string, string>>(() => {
    try { return JSON.parse(localStorage.getItem(NAMES_KEY) ?? '{}') } catch { return {} }
  })
  const setName = (id: string, name: string) => {
    setNamesState(prev => {
      const next = { ...prev, [id]: name }
      localStorage.setItem(NAMES_KEY, JSON.stringify(next))
      return next
    })
  }
  return { names, setName }
}

// ── ────────────────────────────────────────────────────────────────────────────

const SCOPE_BADGE: Record<string, string> = {
  STRATEGY: 'bg-accent-blue/20 text-accent-blue border-accent-blue/30',
  LEG:      'bg-accent-purple/20 text-accent-purple border-accent-purple/30',
  SPOT:     'bg-profit/20 text-profit border-profit/30',
  INDICATOR:'bg-accent-amber/20 text-accent-amber border-accent-amber/30',
  MIXED:    'bg-text-muted/20 text-text-muted border-border-default',
}

function detectScope(tree: AlertRuleBuilderData['condition_tree']): string {
  const scopes = new Set<string>()
  const collect = (node: typeof tree) => {
    node.conditions.forEach(c => scopes.add(c.scope))
    node.groups.forEach(g => collect(g))
  }
  collect(tree)
  if (scopes.size > 1) return 'MIXED'
  return scopes.values().next().value ?? 'STRATEGY'
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
}

interface AlertRowProps {
  rule: AlertRuleBuilderData
  strategyId: string
  onEdit: (rule: AlertRuleBuilderData) => void
}

function AlertRow({ rule, strategyId, onEdit }: AlertRowProps) {
  const qc     = useQueryClient()
  const qKey   = ['alert-rules', strategyId]
  const qKeyAll = ['alert-rules-all']

  const inv = () => {
    qc.invalidateQueries({ queryKey: qKey })
    qc.invalidateQueries({ queryKey: qKeyAll })
  }

  const toggleMut = useMutation({
    mutationFn: () => alertRuleService.toggle(rule.alert_id!),
    onSuccess: inv,
    onError: () => toast.error('Toggle failed'),
  })
  const resetMut = useMutation({
    mutationFn: () => alertRuleService.reset(rule.alert_id!),
    onSuccess: () => { inv(); toast.success('Reset') },
    onError: () => toast.error('Reset failed'),
  })
  const deleteMut = useMutation({
    mutationFn: () => alertRuleService.delete(rule.alert_id!),
    onSuccess: () => { inv(); toast.success('Deleted') },
    onError: () => toast.error('Delete failed'),
  })

  const scope = detectScope(rule.condition_tree)
  const preview = buildPreviewText(rule.condition_tree).split('\n')[0]
  const notifyLabels = [
    rule.notify_popup && 'Popup',
    rule.notify_telegram && 'Telegram',
    rule.notify_sound && 'Sound',
    rule.notify_email && 'Email',
    rule.notify_webhook && 'Webhook',
  ].filter(Boolean)

  return (
    <div className="px-4 py-3 border-b border-border-subtle hover:bg-surface-3/30 transition-colors">
      <div className="flex items-start gap-3">
        {/* Toggle */}
        <button
          onClick={() => toggleMut.mutate()}
          className="mt-0.5 shrink-0 text-text-muted hover:text-accent-blue transition-colors"
          title={rule.is_active ? 'Disable' : 'Enable'}
        >
          {rule.is_active
            ? <ToggleRight size={16} className="text-accent-blue" />
            : <ToggleLeft size={16} />}
        </button>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={cn('text-sm font-medium', rule.is_active ? 'text-text-primary' : 'text-text-muted line-through')}>
              {rule.name}
            </span>
            <span className={cn('text-[10px] px-1.5 py-0.5 rounded border font-medium', SCOPE_BADGE[scope])}>
              {scope}
            </span>
            {(rule.triggered_count ?? 0) > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent-amber/10 text-accent-amber border border-accent-amber/20">
                ×{rule.triggered_count}
              </span>
            )}
          </div>
          <p className="text-[11px] text-text-muted font-mono mt-1 truncate">{preview}</p>
          <div className="flex items-center gap-3 mt-1">
            {notifyLabels.length > 0 && (
              <span className="text-[10px] text-text-muted">
                Notify: {notifyLabels.join(', ')}
              </span>
            )}
            {rule.last_triggered && (
              <span className="text-[10px] text-text-muted">
                Last: {fmtTime(rule.last_triggered)}
              </span>
            )}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => onEdit(rule)}
            className="p-1 text-text-muted hover:text-text-primary transition-colors"
            title="Edit"
          >
            <Edit2 size={12} />
          </button>
          <button
            onClick={() => resetMut.mutate()}
            className="p-1 text-text-muted hover:text-accent-blue transition-colors"
            title="Reset count"
          >
            <RotateCcw size={12} />
          </button>
          <button
            onClick={() => { if (confirm('Delete alert rule?')) deleteMut.mutate() }}
            className="p-1 text-text-muted hover:text-loss transition-colors"
            title="Delete"
          >
            <Trash2 size={12} />
          </button>
        </div>
      </div>
    </div>
  )
}

interface PositionGroupProps {
  strategyId: string
  strategyName: string
  underlyingSymbol?: string
  rules: AlertRuleBuilderData[]
  positionLegs: Array<{ leg_id: string; symbol: string; side: 'BUY' | 'SELL' }>
  onRename: (name: string) => void
}

function PositionGroup({ strategyId, strategyName, underlyingSymbol, rules, positionLegs, onRename }: PositionGroupProps) {
  const [expanded,  setExpanded ] = useState(true)
  const [listOpen,  setListOpen ] = useState(false)
  const [editing,   setEditing  ] = useState(false)
  const [draftName, setDraftName] = useState(strategyName)
  const inputRef = useRef<HTMLInputElement>(null)

  // Keep draft in sync if prop changes from outside
  useEffect(() => { if (!editing) setDraftName(strategyName) }, [strategyName, editing])

  // Focus input when editing starts
  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  const commitRename = () => {
    const trimmed = draftName.trim()
    if (trimmed && trimmed !== strategyName) onRename(trimmed)
    else setDraftName(strategyName)
    setEditing(false)
  }

  return (
    <div className="border border-border-subtle rounded-lg overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center justify-between px-4 py-3 bg-surface-2 cursor-pointer hover:bg-surface-3 transition-colors"
        onClick={() => { if (!editing) setExpanded(e => !e) }}
      >
        <div className="flex items-center gap-2 min-w-0 flex-1">
          {expanded ? <ChevronDown size={13} className="text-text-muted shrink-0" /> : <ChevronRight size={13} className="text-text-muted shrink-0" />}

          {/* Inline name edit */}
          {editing ? (
            <div className="flex items-center gap-1.5" onClick={e => e.stopPropagation()}>
              <input
                ref={inputRef}
                value={draftName}
                onChange={e => setDraftName(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') { setDraftName(strategyName); setEditing(false) } }}
                onBlur={commitRename}
                className="text-sm font-semibold bg-surface-3 border border-accent-blue rounded px-2 py-0.5 text-text-primary focus:outline-none w-48"
              />
              <button onClick={commitRename} className="p-0.5 text-profit hover:text-profit/80 transition-colors" title="Save">
                <Check size={13} />
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-sm font-semibold text-text-primary truncate">{strategyName}</span>
              <button
                onClick={e => { e.stopPropagation(); setEditing(true) }}
                className="p-0.5 text-text-muted hover:text-text-primary transition-colors shrink-0"
                title="Rename"
              >
                <Edit2 size={11} />
              </button>
            </div>
          )}

          {underlyingSymbol && <span className="text-xs text-text-muted shrink-0">{underlyingSymbol}</span>}
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-3 text-text-muted border border-border-subtle shrink-0">
            {rules.length} alert{rules.length !== 1 ? 's' : ''}
          </span>
        </div>
        <button
          onClick={e => { e.stopPropagation(); setListOpen(true) }}
          className="flex items-center gap-1 px-2 py-1 text-[11px] text-accent-blue border border-accent-blue/40 rounded hover:bg-accent-blue/10 transition-colors shrink-0 ml-2"
        >
          <Plus size={11} />
          Add Alert
        </button>
      </div>

      {/* Alert rows */}
      {expanded && (
        rules.length === 0 ? (
          <div className="px-4 py-4 text-center text-xs text-text-muted">
            No alerts for this position.{' '}
            <button onClick={() => setListOpen(true)} className="text-accent-blue hover:underline">Add one</button>
          </div>
        ) : rules.map(rule => (
          <AlertRow
            key={rule.alert_id}
            rule={rule}
            strategyId={strategyId}
            onEdit={() => setListOpen(true)}
          />
        ))
      )}

      {/* AlertList dialog */}
      <Dialog.Root open={listOpen} onOpenChange={setListOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[560px] max-w-[95vw] h-[580px] bg-surface-1 border border-border-default rounded-xl shadow-modal overflow-hidden flex flex-col">
            <Dialog.Title className="sr-only">Alert Rules — {strategyName}</Dialog.Title>
            <AlertList
              strategyId={strategyId}
              strategyName={strategyName}
              positionLegs={positionLegs}
            />
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  )
}

export function AlertsByPosition() {
  const [search, setSearch] = useState('')
  const strategies = useStrategyStore(s => s.strategies)
  const { names: customNames, setName } = useGroupNames()

  // Fetch all rules at once
  const { data: allRules = [], isLoading } = useQuery({
    queryKey: ['alert-rules-all'],
    queryFn: () => alertRuleService.listAll(),
    staleTime: 10_000,
  })

  // Build groups from allRules (backend source of truth) — this ensures alerts
  // are visible even if the strategy is no longer in the Zustand store
  const strategyIdsWithAlerts = [...new Set(allRules.map(r => r.strategy_id))]

  const groupsWithAlerts = strategyIdsWithAlerts.map(strategyId => {
    const storeStrategy  = strategies.find(s => s.id === strategyId)
    // Name priority: Zustand store → custom name saved by user → fallback ID
    const strategyName =
      storeStrategy?.name ??
      customNames[strategyId] ??
      `Strategy ${strategyId.slice(0, 8)}…`
    return {
      strategyId,
      strategyName,
      underlyingSymbol: storeStrategy?.underlyingSymbol,
      rules:            allRules.filter(r => r.strategy_id === strategyId),
      positionLegs:     storeStrategy?.legs.map(l => ({
        leg_id: l.id,
        symbol: l.instrument.symbol,
        side:   l.side,
      })) ?? [],
    }
  })

  // Also include strategies from the store that have no alerts yet
  const withAlertsSet = new Set(strategyIdsWithAlerts)
  const groupsNoAlerts = strategies
    .filter(s => !withAlertsSet.has(s.id))
    .map(s => ({
      strategyId:       s.id,
      strategyName:     s.name,
      underlyingSymbol: s.underlyingSymbol,
      rules:            [] as AlertRuleBuilderData[],
      positionLegs:     s.legs.map(l => ({
        leg_id: l.id,
        symbol: l.instrument.symbol,
        side:   l.side,
      })),
    }))

  const allGroups = [...groupsWithAlerts, ...groupsNoAlerts]

  const filtered = allGroups.filter(g =>
    g.strategyName.toLowerCase().includes(search.toLowerCase()) ||
    (g.underlyingSymbol ?? '').toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="flex flex-col gap-4 p-6">
      {/* Search */}
      <div className="relative">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search by strategy name or underlying…"
          className="w-full pl-9 pr-3 py-2 text-sm bg-surface-2 border border-border-default rounded-lg text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue"
        />
      </div>

      {isLoading ? (
        <div className="text-center py-12 text-xs text-text-muted">Loading…</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-12 text-xs text-text-muted">
          {search ? 'No positions match your search.' : 'No positions or alerts found.'}
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map(g => (
            <PositionGroup
              key={g.strategyId}
              strategyId={g.strategyId}
              strategyName={g.strategyName}
              underlyingSymbol={g.underlyingSymbol}
              rules={g.rules}
              positionLegs={g.positionLegs}
              onRename={name => setName(g.strategyId, name)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
