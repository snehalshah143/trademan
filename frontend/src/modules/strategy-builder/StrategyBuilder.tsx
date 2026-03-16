import { useState, useEffect, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Play, BellPlus } from 'lucide-react'
import * as Tabs from '@radix-ui/react-tabs'
import { useStrategyStore } from '@store/strategyStore'
import { useLTPStore } from '@store/ltpStore'
import { INSTRUMENTS } from './InstrumentSelector'
import { getStaticInstrument } from '@/data/instruments'
import { InstrumentHeader } from './PositionsTab'
import { OptionChainView } from './OptionChainView'
import { LegEditor } from './LegEditor'
import { PositionsTab } from './PositionsTab'
import { PayoffPanel } from './PayoffPanel'
import { ExecuteModal } from './ExecuteModal'
import { AlertConfigModal } from '@modules/position-manager/AlertConfigModal'
import type { StrategyLeg, Strategy } from '@/types/domain'

export function StrategyBuilder() {
  const navigate = useNavigate()
  const { draftStrategy, setDraft, updateDraft, clearDraft } = useStrategyStore()
  const niftyLTP = useLTPStore((s) => s.getLTP('NIFTY')) ?? 22500

  const [activeTab, setActiveTab] = useState('optionchain')
  const [executeOpen, setExecuteOpen] = useState(false)
  const [alertsOpen, setAlertsOpen] = useState(false)

  // Shared header state — visible above ALL tabs
  const [selectedExpiry, setSelectedExpiry] = useState<string | null>(null)   // option chain expiry (can be weekly)
  const [selectedFutExpiry, setSelectedFutExpiry] = useState<string | null>(null) // FUT expiry (monthly only)
  const [addFormSide, setAddFormSide] = useState<'BUY' | 'SELL' | null>(null)

  // Track which legs are enabled for payoff computation
  const [enabledLegs, setEnabledLegs] = useState<Set<string>>(new Set())

  // Init draft on mount
  useEffect(() => {
    if (!draftStrategy) {
      setDraft({
        name: 'New Strategy',
        underlyingSymbol: 'NIFTY',
        legs: [],
        status: 'DRAFT',
        tags: [],
      })
    }
  }, [draftStrategy, setDraft])

  // When new legs are added, auto-enable them
  useEffect(() => {
    if (!draftStrategy) return
    setEnabledLegs((prev) => {
      const next = new Set(prev)
      for (const leg of draftStrategy.legs) {
        if (!next.has(leg.id)) next.add(leg.id)
      }
      // Remove stale IDs
      for (const id of next) {
        if (!draftStrategy.legs.find((l) => l.id === id)) next.delete(id)
      }
      return next
    })
  }, [draftStrategy?.legs])

  const legs = draftStrategy?.legs ?? []
  const underlying = draftStrategy?.underlyingSymbol ?? 'NIFTY'

  const lotSize = useMemo(
    () => getStaticInstrument(underlying)?.lotSize ?? INSTRUMENTS.find((i) => i.symbol === underlying)?.lotSize ?? 75,
    [underlying]
  )

  // Expiry — use first available or empty
  const expiry = useMemo(() => {
    const legWithExpiry = legs.find((l) => l.instrument.expiry)
    return legWithExpiry?.instrument.expiry ?? ''
  }, [legs])

  // Build strategy for payoff (only enabled legs)
  const strategyForPayoff: Strategy | null = useMemo(() => {
    if (!draftStrategy) return null
    const enabledLegsList = legs.filter((l) => enabledLegs.has(l.id))
    return {
      id: 'draft',
      name: draftStrategy.name,
      underlyingSymbol: underlying,
      legs: enabledLegsList,
      status: 'DRAFT',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    }
  }, [draftStrategy, legs, enabledLegs, underlying])

  const currentSpot = useLTPStore((s) => s.getLTP(underlying)) ?? niftyLTP

  // ─── Handlers ───────────────────────────────────────────────────────────────

  const handleInstrumentChange = useCallback((symbol: string, _lotSize: number) => {
    if (!draftStrategy) return
    if (legs.length > 0) {
      if (!confirm(`Change underlying to ${symbol}? This will clear all legs.`)) return
    }
    updateDraft({ underlyingSymbol: symbol, legs: [] })
    setEnabledLegs(new Set())
  }, [draftStrategy, legs, updateDraft])

  const handleLegsChange = useCallback((newLegs: StrategyLeg[]) => {
    if (!draftStrategy) return
    updateDraft({ legs: newLegs.map((l, i) => ({ ...l, legIndex: i })) })
  }, [draftStrategy, updateDraft])

  const handleAddLeg = useCallback((leg: StrategyLeg) => {
    if (!draftStrategy) return
    updateDraft({ legs: [...legs, { ...leg, legIndex: legs.length }] })
  }, [draftStrategy, legs, updateDraft])

  const handleRemoveLeg = useCallback((legId: string) => {
    updateDraft({ legs: legs.filter((l) => l.id !== legId) })
  }, [legs, updateDraft])

  const handleToggleLeg = useCallback((id: string, enabled: boolean) => {
    setEnabledLegs((prev) => {
      const next = new Set(prev)
      enabled ? next.add(id) : next.delete(id)
      return next
    })
  }, [])

  const handleEditLots = useCallback((id: string, lots: number) => {
    const leg = legs.find((l) => l.id === id)
    if (!leg) return
    updateDraft({
      legs: legs.map((l) => l.id === id ? { ...l, lots, quantity: lots * l.instrument.lotSize } : l)
    })
  }, [legs, updateDraft])

  const handleToggleSide = useCallback((id: string) => {
    updateDraft({
      legs: legs.map((l) => l.id === id
        ? { ...l, side: l.side === 'BUY' ? 'SELL' : 'BUY', isHedge: l.side === 'BUY' }
        : l
      )
    })
  }, [legs, updateDraft])

  const handleEditEntryPrice = useCallback((id: string, price: number) => {
    updateDraft({
      legs: legs.map((l) => l.id === id ? { ...l, entryPrice: price } : l)
    })
  }, [legs, updateDraft])

  const handleLoadPreset = useCallback((presetLegs: StrategyLeg[]) => {
    if (!draftStrategy) return
    updateDraft({ legs: presetLegs.map((l, i) => ({ ...l, legIndex: i })) })
  }, [draftStrategy, updateDraft])

  const handleClearLegs = useCallback(() => {
    if (!confirm('Clear all legs?')) return
    updateDraft({ legs: [] })
    setEnabledLegs(new Set())
  }, [updateDraft])

  // B/S from shared header: open add-leg form AND switch to Positions tab
  const handleBSClick = useCallback((side: 'BUY' | 'SELL') => {
    setAddFormSide((prev) => (prev === side ? null : side))
    setActiveTab('positions')
  }, [])

  const handleExecuteSuccess = useCallback(() => {
    clearDraft()
    navigate('/')
  }, [clearDraft, navigate])

  if (!draftStrategy) return null

  return (
    <div className="flex h-full">
      {/* ── LEFT PANEL (55%) ── */}
      <div className="w-[55%] flex flex-col h-full border-r border-border-subtle overflow-hidden">

        {/* Shared instrument header — always visible above all tabs */}
        <InstrumentHeader
          underlying={underlying}
          lotSize={lotSize}
          legs={legs}
          selectedFutExpiry={selectedFutExpiry}
          addFormSide={addFormSide}
          onSelectFutExpiry={setSelectedFutExpiry}
          onBSClick={handleBSClick}
          onClearLegs={handleClearLegs}
          onChangeUnderlying={handleInstrumentChange}
        />

        {/* Tabs */}
        <Tabs.Root value={activeTab} onValueChange={setActiveTab} className="flex flex-col flex-1 min-h-0 overflow-hidden">
          <Tabs.List className="flex border-b border-border-subtle bg-surface-1 shrink-0 px-3 gap-0">
            {[
              { value: 'optionchain', label: 'Option Chain' },
              { value: 'legeditor',   label: 'Leg Editor'   },
              { value: 'positions',   label: `Positions${legs.length > 0 ? ` (${legs.length})` : ''}` },
            ].map((tab) => (
              <Tabs.Trigger
                key={tab.value}
                value={tab.value}
                className="px-3 py-2.5 text-xs font-medium border-b-2 data-[state=active]:border-accent-blue data-[state=active]:text-text-primary border-transparent text-text-muted hover:text-text-secondary transition-colors"
              >
                {tab.label}
              </Tabs.Trigger>
            ))}
          </Tabs.List>

          <div className="flex-1 min-h-0 overflow-hidden">
            <Tabs.Content value="optionchain" className="h-full overflow-hidden flex flex-col data-[state=inactive]:hidden">
              <OptionChainView
                underlying={underlying}
                lotSize={lotSize}
                legs={legs}
                onLegsChange={handleLegsChange}
                controlledExpiry={selectedExpiry ?? undefined}
                onExpiryChange={setSelectedExpiry}
              />
            </Tabs.Content>

            <Tabs.Content value="legeditor" className="h-full overflow-y-auto custom-scroll p-4 space-y-4 data-[state=inactive]:hidden">
              <LegEditor onAddLeg={handleAddLeg} defaultUnderlying={underlying} />

              {legs.length > 0 && (
                <div className="space-y-1">
                  <div className="text-xs text-text-muted mb-2">Added Legs ({legs.length})</div>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-[10px] text-text-muted border-b border-border-subtle">
                        <th className="text-left px-2 py-1">Identifier</th>
                        <th className="px-2 py-1">Side</th>
                        <th className="px-2 py-1">Lots</th>
                        <th className="px-2 py-1 w-8"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {legs.map((leg) => (
                        <tr key={leg.id} className="border-b border-border-subtle">
                          <td className="px-2 py-1.5 text-left font-mono text-text-secondary text-[11px]">
                            {leg.instrument.symbol}
                          </td>
                          <td className="px-2 py-1.5 text-center">
                            <span className={`text-xs font-medium ${leg.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                              {leg.side}
                            </span>
                          </td>
                          <td className="px-2 py-1.5 text-center font-mono">{leg.lots}</td>
                          <td className="px-2 py-1.5 text-center">
                            <button
                              onClick={() => handleRemoveLeg(leg.id)}
                              className="text-text-muted hover:text-loss text-xs"
                            >
                              ×
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Tabs.Content>

            <Tabs.Content value="positions" className="h-full overflow-hidden flex flex-col data-[state=inactive]:hidden">
              <PositionsTab
                underlying={underlying}
                lotSize={lotSize}
                legs={legs}
                enabledLegs={enabledLegs}
                expiry={expiry}
                addFormSide={addFormSide}
                selectedFutExpiry={selectedFutExpiry}
                onAddLeg={handleAddLeg}
                onSetAddFormSide={setAddFormSide}
                onToggleLeg={handleToggleLeg}
                onEditLots={handleEditLots}
                onToggleSide={handleToggleSide}
                onRemoveLeg={handleRemoveLeg}
                onEditEntryPrice={handleEditEntryPrice}
                onLoadPreset={handleLoadPreset}
                onSwitchToOptionChain={() => setActiveTab('optionchain')}
                onSwitchToLegEditor={() => setActiveTab('legeditor')}
              />
            </Tabs.Content>
          </div>
        </Tabs.Root>

        {/* Execute bar */}
        <div className="shrink-0 px-4 py-3 border-t border-border-subtle bg-surface-1 space-y-2">
          <div className="flex items-center gap-2">
            <input
              value={draftStrategy.name}
              onChange={(e) => updateDraft({ name: e.target.value })}
              placeholder="Strategy name…"
              className="flex-1 px-3 py-1.5 text-sm bg-surface-3 border border-border-default rounded-md text-text-primary focus:outline-none focus:border-accent-blue"
            />
            {legs.length > 0 && (
              <button
                onClick={() => setAlertsOpen(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-border-default text-text-secondary hover:border-accent-amber hover:text-accent-amber rounded-md transition-colors shrink-0"
                title="Add/Edit Alerts"
              >
                <BellPlus size={13} />
                Alerts
              </button>
            )}
          </div>
          <button
            disabled={legs.length === 0 || !draftStrategy.name.trim()}
            onClick={() => setExecuteOpen(true)}
            className="w-full flex items-center justify-center gap-2 py-2.5 text-sm font-semibold bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Play size={14} />
            Execute Strategy
          </button>
        </div>
      </div>

      {/* ── RIGHT PANEL (45%) ── */}
      <div className="flex-1 overflow-hidden">
        <PayoffPanel
          strategy={strategyForPayoff}
          currentSpot={currentSpot}
        />
      </div>

      {executeOpen && draftStrategy && (
        <ExecuteModal
          open={executeOpen}
          onOpenChange={(open) => {
            if (!open) handleExecuteSuccess()
            setExecuteOpen(open)
          }}
          draft={{
            ...draftStrategy,
            legs: legs,
          }}
        />
      )}

      {alertsOpen && draftStrategy && (
        <AlertConfigModal
          open={alertsOpen}
          onOpenChange={setAlertsOpen}
          strategy={{
            id: 'draft',
            name: draftStrategy.name ?? 'Draft',
            underlyingSymbol: underlying,
            legs,
            status: 'DRAFT',
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          }}
        />
      )}
    </div>
  )
}
