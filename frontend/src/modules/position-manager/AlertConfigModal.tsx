import { useState, useMemo } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import * as Switch from '@radix-ui/react-switch'
import { X, ChevronDown, ChevronUp } from 'lucide-react'
import axios from 'axios'
import toast from 'react-hot-toast'
import { format, parseISO } from 'date-fns'
import { useStrategyStore } from '@store/strategyStore'
import { useLTPStore } from '@store/ltpStore'
import { fmtPrice, cn } from '@/lib/utils'
import type {
  Strategy,
  AlertRules,
  StrategyLeg,
} from '@/types/domain'

// ─── Types ────────────────────────────────────────────────────────────────────

interface LegAlertState { target: string; sl: string }
type Operator = 'less_than' | 'greater_than' | 'equal_to'

interface AlertConfigModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  strategy: Strategy
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function legLabel(leg: StrategyLeg) {
  const { instrument, lots } = leg
  const expStr = instrument.expiry ? format(parseISO(instrument.expiry), 'dd MMM') : ''
  const strikeStr = instrument.strike != null ? String(instrument.strike) : ''
  return `${expStr} ${strikeStr} ${instrument.instrumentType} × ${lots}`.trim()
}

function operatorLabel(op: string) {
  return op === 'less_than' ? 'Less Than' : op === 'greater_than' ? 'Greater Than' : 'Equal To'
}

function SwitchBtn({
  checked,
  onCheckedChange,
}: {
  checked: boolean
  onCheckedChange: (v: boolean) => void
}) {
  return (
    <Switch.Root
      checked={checked}
      onCheckedChange={onCheckedChange}
      className="w-8 h-4 bg-surface-3 border border-border-default rounded-full data-[state=checked]:bg-accent-blue transition-colors shrink-0"
    >
      <Switch.Thumb className="block w-3 h-3 bg-text-muted rounded-full translate-x-0.5 transition-transform data-[state=checked]:translate-x-4 data-[state=checked]:bg-white" />
    </Switch.Root>
  )
}

const inputCls =
  'flex-1 px-2 py-1 text-xs bg-surface-3 border border-border-default rounded outline-none focus:border-accent-blue disabled:opacity-40 text-text-primary text-right min-w-0'
const selectCls =
  'px-2 py-1 text-xs bg-surface-3 border border-border-default rounded outline-none text-text-primary disabled:opacity-40 shrink-0'

// ─── Component ────────────────────────────────────────────────────────────────

export function AlertConfigModal({ open, onOpenChange, strategy }: AlertConfigModalProps) {
  const { updateDraft } = useStrategyStore()
  const ltpMap = useLTPStore((s) => s.ltpMap)

  // ── Live header values ────────────────────────────────────────────────────

  const spot = ltpMap[strategy.underlyingSymbol]?.tick.ltp ?? 0

  const mtm = useMemo(
    () =>
      strategy.legs.reduce((sum, leg) => {
        const ltp = ltpMap[leg.instrument.symbol]?.tick.ltp
        if (!ltp || leg.entryPrice == null) return sum
        return sum + (leg.side === 'BUY' ? 1 : -1) * (ltp - leg.entryPrice) * leg.quantity
      }, 0),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [strategy.legs, ltpMap]
  )

  const netDelta = useMemo(
    () =>
      strategy.legs.reduce((sum, leg) => {
        const mult = leg.side === 'BUY' ? 1 : -1
        const base =
          leg.instrument.instrumentType === 'FUT'
            ? 1
            : leg.instrument.instrumentType === 'CE'
            ? 0.5
            : -0.5
        return sum + mult * base * leg.lots
      }, 0),
    [strategy.legs]
  )

  // ── Section collapse ─────────────────────────────────────────────────────

  const [positionOpen, setPositionOpen] = useState(true)
  const [overallOpen, setOverallOpen] = useState(true)

  // ── Per-leg alert state ──────────────────────────────────────────────────

  const [legAlerts, setLegAlerts] = useState<Record<string, LegAlertState>>(() => {
    const init: Record<string, LegAlertState> = {}
    for (const leg of strategy.legs) init[leg.id] = { target: '', sl: '' }
    return init
  })

  const setLegField = (id: string, field: 'target' | 'sl', val: string) =>
    setLegAlerts((prev) => ({ ...prev, [id]: { ...prev[id], [field]: val } }))

  // ── Overall alert state ──────────────────────────────────────────────────

  const [overallTarget, setOverallTarget] = useState({ enabled: false, value: '0' })
  const [overallSL, setOverallSL] = useState({ enabled: false, value: '0' })
  const [underlyingAlert, setUnderlyingAlert] = useState({
    enabled: false,
    operator: 'less_than' as Operator,
    value: spot > 0 ? String(Math.round(spot)) : '0',
  })
  const [deltaAlert, setDeltaAlert] = useState({
    enabled: false,
    operator: 'less_than' as 'less_than' | 'greater_than',
    value: netDelta.toFixed(3),
  })

  const lotSize = strategy.legs[0]?.instrument.lotSize ?? 75
  const deltaInRupees = (parseFloat(deltaAlert.value) || 0) * lotSize

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleClear = () => {
    const cleared: Record<string, LegAlertState> = {}
    for (const leg of strategy.legs) cleared[leg.id] = { target: '', sl: '' }
    setLegAlerts(cleared)
    setOverallTarget({ enabled: false, value: '0' })
    setOverallSL({ enabled: false, value: '0' })
    setUnderlyingAlert({ enabled: false, operator: 'less_than', value: spot > 0 ? String(Math.round(spot)) : '0' })
    setDeltaAlert({ enabled: false, operator: 'less_than', value: netDelta.toFixed(3) })
  }

  const handleDone = async () => {
    const rules: AlertRules = {
      positionAlerts: strategy.legs.map((leg) => ({
        legId: leg.id,
        targetPrice: parseFloat(legAlerts[leg.id]?.target) || null,
        slPrice: parseFloat(legAlerts[leg.id]?.sl) || null,
      })),
      overallTarget: overallTarget.enabled
        ? { enabled: true, mtmValue: parseFloat(overallTarget.value) || 0 }
        : null,
      overallStopLoss: overallSL.enabled
        ? { enabled: true, mtmValue: parseFloat(overallSL.value) || 0 }
        : null,
      underlyingAlert: underlyingAlert.enabled
        ? { enabled: true, operator: underlyingAlert.operator, value: parseFloat(underlyingAlert.value) || 0 }
        : null,
      deltaAlert: deltaAlert.enabled
        ? { enabled: true, operator: deltaAlert.operator, value: parseFloat(deltaAlert.value) || 0 }
        : null,
    }

    if (strategy.id === 'draft') {
      updateDraft({ alertRules: rules } as Parameters<typeof updateDraft>[0])
    } else {
      try {
        await axios.put(`/api/v1/strategies/${strategy.id}/alert-rules`, rules)
      } catch {
        toast.error('Failed to save alert rules')
        return
      }
    }
    toast.success('Alert rules saved')
    onOpenChange(false)
  }

  // ─── Render ──────────────────────────────────────────────────────────────

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-full max-w-2xl bg-surface-1 border border-border-default rounded-lg shadow-modal animate-fade-in flex flex-col max-h-[90vh]">

          {/* Title bar */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle shrink-0">
            <Dialog.Title className="text-text-primary font-semibold">Add Alerts</Dialog.Title>
            <Dialog.Close asChild>
              <button className="text-text-muted hover:text-text-primary"><X size={16} /></button>
            </Dialog.Close>
          </div>

          {/* Live metrics bar */}
          <div className="flex items-center gap-6 px-5 py-2.5 bg-surface-2 border-b border-border-subtle shrink-0">
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-text-muted">MTM</span>
              <span className={cn('text-xs font-mono font-medium', mtm >= 0 ? 'text-profit' : 'text-loss')}>
                {mtm >= 0 ? '+' : ''}{fmtPrice(mtm)}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-text-muted">Underlying</span>
              <span className="text-xs font-mono text-text-primary">{spot > 0 ? fmtPrice(spot) : '—'}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-text-muted">Delta</span>
              <span className="text-xs font-mono text-text-primary">{netDelta.toFixed(3)}</span>
            </div>
          </div>

          {/* Scrollable body */}
          <div className="flex-1 overflow-y-auto custom-scroll">

            {/* ── Section 1: Position Alerts ────────────────────────────── */}
            <div className="border-b border-border-subtle">
              <button
                onClick={() => setPositionOpen((v) => !v)}
                className="w-full flex items-center justify-between px-5 py-3 text-sm font-semibold text-text-primary hover:bg-surface-2 transition-colors"
              >
                <span>Position Alerts</span>
                {positionOpen ? <ChevronUp size={14} className="text-text-muted" /> : <ChevronDown size={14} className="text-text-muted" />}
              </button>

              {positionOpen && (
                strategy.legs.length === 0 ? (
                  <div className="px-5 pb-4 text-xs text-text-muted">No legs in strategy</div>
                ) : (
                  <div className="px-5 pb-4 overflow-x-auto">
                    <table className="w-full text-xs min-w-[500px]">
                      <thead>
                        <tr className="text-[10px] text-text-muted uppercase border-b border-border-subtle">
                          <th className="py-1.5 text-left">Identifier</th>
                          <th className="py-1.5 text-right pr-3">LTP</th>
                          <th className="py-1.5 text-right pr-2 w-32">Target (₹)</th>
                          <th className="py-1.5 text-right w-32">Stop Loss (₹)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {strategy.legs.map((leg) => {
                          const ltp = ltpMap[leg.instrument.symbol]?.tick.ltp ?? leg.entryPrice ?? 0
                          const st = legAlerts[leg.id] ?? { target: '', sl: '' }
                          return (
                            <tr key={leg.id} className="border-b border-border-subtle last:border-0">
                              <td className="py-2 pr-3">
                                <div className="flex items-center gap-1.5">
                                  <span className={cn(
                                    'px-1.5 py-0.5 rounded text-[9px] font-bold shrink-0',
                                    leg.side === 'BUY'
                                      ? 'bg-accent-blue/20 text-accent-blue'
                                      : 'bg-red-500/20 text-red-400'
                                  )}>
                                    {leg.side === 'BUY' ? 'B' : 'S'}
                                  </span>
                                  <span className="text-text-secondary font-mono text-[11px] whitespace-nowrap">{legLabel(leg)}</span>
                                </div>
                              </td>
                              <td className="py-2 pr-3 text-right font-mono text-text-primary whitespace-nowrap">
                                {ltp > 0 ? ltp.toFixed(2) : '—'}
                              </td>
                              <td className="py-2 pr-2">
                                <input
                                  type="number"
                                  placeholder="Enter Value"
                                  value={st.target}
                                  onChange={(e) => setLegField(leg.id, 'target', e.target.value)}
                                  className="w-full px-2 py-1 text-xs text-right bg-surface-3 border border-border-default rounded outline-none focus:border-accent-blue text-text-primary placeholder-text-muted"
                                />
                              </td>
                              <td className="py-2">
                                <input
                                  type="number"
                                  placeholder="Enter Value"
                                  value={st.sl}
                                  onChange={(e) => setLegField(leg.id, 'sl', e.target.value)}
                                  className="w-full px-2 py-1 text-xs text-right bg-surface-3 border border-border-default rounded outline-none focus:border-accent-blue text-text-primary placeholder-text-muted"
                                />
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )
              )}
            </div>

            {/* ── Section 2: Overall Alerts ─────────────────────────────── */}
            <div>
              <button
                onClick={() => setOverallOpen((v) => !v)}
                className="w-full flex items-center justify-between px-5 py-3 text-sm font-semibold text-text-primary hover:bg-surface-2 transition-colors"
              >
                <span>Overall Alerts</span>
                {overallOpen ? <ChevronUp size={14} className="text-text-muted" /> : <ChevronDown size={14} className="text-text-muted" />}
              </button>

              {overallOpen && (
                <div className="px-5 pb-5 grid grid-cols-2 gap-3">

                  {/* Overall Target */}
                  <div className="border border-border-subtle rounded-lg p-3 space-y-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-text-primary">Overall Target</span>
                      <SwitchBtn
                        checked={overallTarget.enabled}
                        onCheckedChange={(v) => setOverallTarget((p) => ({ ...p, enabled: v }))}
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-text-muted shrink-0">MTM</span>
                      <input
                        type="number"
                        placeholder="0"
                        value={overallTarget.value}
                        onChange={(e) => setOverallTarget((p) => ({ ...p, value: e.target.value }))}
                        disabled={!overallTarget.enabled}
                        className={inputCls}
                      />
                    </div>
                  </div>

                  {/* Alert when Underlying is */}
                  <div className="border border-border-subtle rounded-lg p-3 space-y-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-text-primary">Alert when Underlying is</span>
                      <SwitchBtn
                        checked={underlyingAlert.enabled}
                        onCheckedChange={(v) => setUnderlyingAlert((p) => ({ ...p, enabled: v }))}
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <select
                        value={underlyingAlert.operator}
                        onChange={(e) => setUnderlyingAlert((p) => ({ ...p, operator: e.target.value as Operator }))}
                        disabled={!underlyingAlert.enabled}
                        className={selectCls}
                      >
                        <option value="less_than">Less Than</option>
                        <option value="greater_than">Greater Than</option>
                        <option value="equal_to">Equal To</option>
                      </select>
                      <input
                        type="number"
                        value={underlyingAlert.value}
                        onChange={(e) => setUnderlyingAlert((p) => ({ ...p, value: e.target.value }))}
                        disabled={!underlyingAlert.enabled}
                        className={inputCls}
                      />
                    </div>
                  </div>

                  {/* Overall Stop Loss */}
                  <div className="border border-border-subtle rounded-lg p-3 space-y-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-text-primary">Overall Stop Loss</span>
                      <SwitchBtn
                        checked={overallSL.enabled}
                        onCheckedChange={(v) => setOverallSL((p) => ({ ...p, enabled: v }))}
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-text-muted shrink-0">MTM</span>
                      <input
                        type="number"
                        placeholder="0"
                        value={overallSL.value}
                        onChange={(e) => setOverallSL((p) => ({ ...p, value: e.target.value }))}
                        disabled={!overallSL.enabled}
                        className={inputCls}
                      />
                    </div>
                  </div>

                  {/* Alert when Delta is */}
                  <div className="border border-border-subtle rounded-lg p-3 space-y-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-text-primary">Alert when Delta is</span>
                      <SwitchBtn
                        checked={deltaAlert.enabled}
                        onCheckedChange={(v) => setDeltaAlert((p) => ({ ...p, enabled: v }))}
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <select
                        value={deltaAlert.operator}
                        onChange={(e) => setDeltaAlert((p) => ({ ...p, operator: e.target.value as 'less_than' | 'greater_than' }))}
                        disabled={!deltaAlert.enabled}
                        className={selectCls}
                      >
                        <option value="less_than">Less Than</option>
                        <option value="greater_than">Greater Than</option>
                      </select>
                      <input
                        type="number"
                        step="0.001"
                        value={deltaAlert.value}
                        onChange={(e) => setDeltaAlert((p) => ({ ...p, value: e.target.value }))}
                        disabled={!deltaAlert.enabled}
                        className={inputCls}
                      />
                    </div>
                    {deltaAlert.enabled && (
                      <p className="text-[10px] text-text-muted">
                        Delta in ₹ is {operatorLabel(deltaAlert.operator)} {deltaInRupees.toFixed(2)}
                      </p>
                    )}
                  </div>

                </div>
              )}
            </div>
          </div>

          {/* Footer */}
          <div className="flex items-center justify-between px-5 py-4 border-t border-border-subtle shrink-0">
            <button
              onClick={handleClear}
              className="text-xs text-loss hover:text-red-400 transition-colors"
            >
              Clear all alerts
            </button>
            <div className="flex items-center gap-2">
              <Dialog.Close asChild>
                <button className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary bg-surface-3 border border-border-default rounded-md transition-colors">
                  Cancel
                </button>
              </Dialog.Close>
              <button
                onClick={handleDone}
                className="px-4 py-2 text-sm font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
              >
                Done
              </button>
            </div>
          </div>

        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
