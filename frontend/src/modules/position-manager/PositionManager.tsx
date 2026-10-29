import { useState, useEffect, useCallback } from 'react'
import { Layers, Plus, RefreshCw, CheckCircle2, AlertCircle } from 'lucide-react'
import axios from 'axios'
import { useLivePositions } from '@hooks/useLivePositions'
import { useBrokerPositions } from '@hooks/useBrokerPositions'
import { useAlertStore } from '@store/alertStore'
import { useLTPStore } from '@store/ltpStore'
import { useStrategyStore } from '@store/strategyStore'
import type { Strategy, StrategyLeg, Exchange, InstrumentType } from '@/types/domain'
import { useOpenAlgoPortfolio, useInvalidateOAPortfolio } from '@hooks/useOpenAlgoPortfolio'
import type { OAPortfolioEntry } from '@hooks/useOpenAlgoPortfolio'
import { MetricCard } from '@/components/ui/MetricCard'
import { StrategyRow } from './StrategyRow'
import { BrokerPositionsTable } from './BrokerPositionsTable'
import { OpenAlgoBuilderModal } from './OpenAlgoBuilderModal'
import { AlgoTradesTab } from './AlgoTradesTab'
import { fmtINRCompact, profitLossClass, cn } from '@/lib/utils'

type Tab = 'strategies' | 'broker' | 'manual' | 'algo'

/** Convert an OpenAlgo portfolio entry into a Trademan Strategy with a proper UUID. */
function oaEntryToStrategy(entry: OAPortfolioEntry): Strategy {
  const now = new Date().toISOString()
  const legs: StrategyLeg[] = entry.legs.map((leg, i) => ({
    id: crypto.randomUUID(),
    legIndex: i,
    instrument: {
      symbol: leg.symbol,
      exchange: (entry.exchange || 'NFO') as Exchange,
      instrumentType: (
        leg.segment === 'FUTURE' ? 'FUT' : (leg.optionType ?? 'CE')
      ) as InstrumentType,
      expiry: leg.expiry,
      strike: leg.strike,
      lotSize: leg.lotSize,
      tickSize: 0.05,
    },
    side: leg.side,
    lots: leg.lots,
    quantity: leg.lots * leg.lotSize,
    productType: 'NRML',
    orderType: 'MARKET',
    entryPrice: leg.price,
    currentLTP: leg.price,
    status: 'FILLED',
    isHedge: leg.side === 'SELL',
  }))

  return {
    id: crypto.randomUUID(),
    name: entry.name,
    underlyingSymbol: entry.underlying,
    legs,
    status: 'ACTIVE',
    tags: ['oa-import'],
    notes: `oa:${entry.id}`,   // tracks the source OA portfolio ID
    createdAt: entry.created_at ?? now,
    updatedAt: entry.updated_at ?? now,
  }
}

export function PositionManager() {
  const allLivePositions = useLivePositions()
  const unreadCount   = useAlertStore((s) => s.unreadCount)
  const updateBatch   = useLTPStore((s) => s.updateBatch)
  const allStrategies = useStrategyStore((s) => s.strategies)
  const addStrategy   = useStrategyStore((s) => s.addStrategy)
  const removeStrategy = useStrategyStore((s) => s.removeStrategy)

  const [removedIds, setRemovedIds] = useState<Set<string>>(new Set())

  // ── OA Portfolio auto-sync ──────────────────────────────────────────────────
  const {
    data:          oaPortfolioData,
    isFetching:    oaFetching,
    isError:       oaError,
    dataUpdatedAt: oaUpdatedAt,
  } = useOpenAlgoPortfolio()
  const invalidateOAPortfolio = useInvalidateOAPortfolio()

  /**
   * Shared import function — runs both on the periodic poll and on manual "Done".
   * Compares OA entries against already-imported strategies (by `notes: "oa:<id>"`),
   * imports only new ones, persists them to the backend, and auto-switches to the
   * Manual tab so the user sees them immediately.
   */
  const importNewOAEntries = useCallback((entries: OAPortfolioEntry[]) => {
    const { strategies: current, addStrategy: addFn } = useStrategyStore.getState()
    let importedCount = 0
    for (const entry of entries) {
      if (current.some((s) => s.notes === `oa:${entry.id}`)) continue
      const strategy = oaEntryToStrategy(entry)
      addFn(strategy)
      importedCount++
      axios.post('/api/v1/strategies', {
        id:          strategy.id,
        name:        strategy.name,
        description: JSON.stringify(strategy),
        status:      'active',
        underlying:  strategy.underlyingSymbol ?? null,
        expiry:      strategy.underlyingExpiry  ?? null,
      }).catch(() => {})
    }
    if (importedCount > 0) {
      setTab('manual')
    }
    return importedCount
  }, [])

  // Run import diff every time the poll returns fresh data
  useEffect(() => {
    if (!oaPortfolioData?.length) return
    importNewOAEntries(oaPortfolioData)
  }, [oaPortfolioData, importNewOAEntries])

  const handleRemove = (id: string) => {
    removeStrategy(id)
    setRemovedIds((prev) => new Set([...prev, id]))
    axios.delete(`/api/v1/strategies/${id}`).catch(() => {})
  }

  // Sync broker position LTPs into ltpStore so strategy tab stays consistent
  // even before WS subscription kicks in (5s polling fallback)
  const { data: brokerPositions } = useBrokerPositions()
  useEffect(() => {
    if (!brokerPositions?.length) return
    updateBatch(
      brokerPositions
        .filter((p) => p.ltp > 0)
        .map((p) => ({
          symbol:    p.symbol,
          ltp:       p.ltp,
          change:    0,
          changePct: 0,
          timestamp: Date.now(),
        }))
    )
  }, [brokerPositions])

  // On mount: if localStorage was cleared, recover strategies from backend DB
  useEffect(() => {
    if (allStrategies.length > 0) return  // store is healthy, nothing to do
    axios.get<Array<{ description?: string | null }>>('/api/v1/strategies')
      .then(r => {
        for (const row of r.data) {
          if (!row.description) continue
          try {
            const s = JSON.parse(row.description) as Strategy
            if (!s?.id || !s?.name || !Array.isArray(s?.legs)) continue
            addStrategy(s)
          } catch { /* skip malformed rows */ }
        }
      })
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // On mount: tell backend to subscribe all strategy leg symbols for real-time WS ticks
  useEffect(() => {
    const symbols = [
      ...new Set(
        allStrategies.flatMap((s) => s.legs.map((l) => l.instrument.symbol))
      ),
    ].filter(Boolean)
    if (symbols.length === 0) return
    axios.post('/api/v1/symbols/subscribe', { symbols }).catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const [oaBuilderOpen,  setOaBuilderOpen]  = useState(false)
  const [oaSyncError,    setOaSyncError]    = useState(false)

  /**
   * Called when the OA Builder popup closes (auto) or user clicks "Done — Sync Now".
   * Does a direct fetch so the import is guaranteed to complete before the modal
   * closes — independent of TanStack Query's background-refetch timing.
   * The 30-second polling via useEffect handles subsequent auto-sync.
   */
  async function handleOADone() {
    setOaSyncError(false)
    try {
      const res = await fetch('/api/v1/oa-portfolio')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (data.status === 'unconfigured') throw new Error('OPENALGO_DB_PATH not set in backend/.env')
      const items: OAPortfolioEntry[] = data.items ?? []
      importNewOAEntries(items)
      // Also update the TanStack Query cache so the 30-s poll stays in sync
      invalidateOAPortfolio()
    } catch {
      setOaSyncError(true)
    }
    setOaBuilderOpen(false)
  }

  const [tab,         setTab]         = useState<Tab>('broker')
  const [checkedLegs, setCheckedLegs] = useState<Set<string>>(new Set())

  // Split live positions: OA-imported go to Manual tab, others to Strategies tab
  const oaImportedStrategies = allLivePositions.filter(
    (s) => s.tags?.includes('oa-import') && !removedIds.has(s.id)
  )
  const strategies = allLivePositions.filter(
    (s) => !s.tags?.includes('oa-import') && !removedIds.has(s.id)
  )

  const totalMTM   = strategies.reduce((s, st) => s + st.liveMTM, 0)
  const oaTotalMTM = oaImportedStrategies.reduce((s, st) => s + st.liveMTM, 0)

  function handleCheck(legId: string, checked: boolean) {
    setCheckedLegs((prev) => {
      const next = new Set(prev)
      checked ? next.add(legId) : next.delete(legId)
      return next
    })
  }

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex items-center gap-0 border-b border-border-subtle bg-surface-1 px-4 shrink-0">
        <button
          onClick={() => setTab('broker')}
          className={cn(
            'px-4 py-2.5 text-xs font-medium border-b-2 transition-colors',
            tab === 'broker'
              ? 'border-accent-blue text-accent-blue'
              : 'border-transparent text-text-muted hover:text-text-secondary'
          )}
        >
          Broker Positions
        </button>
        <button
          onClick={() => setTab('strategies')}
          className={cn(
            'px-4 py-2.5 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5',
            tab === 'strategies'
              ? 'border-accent-blue text-accent-blue'
              : 'border-transparent text-text-muted hover:text-text-secondary'
          )}
        >
          Strategies
          {strategies.length > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-accent-blue/15 text-accent-blue font-medium">
              {strategies.length}
            </span>
          )}
        </button>
        <button
          onClick={() => setTab('manual')}
          className={cn(
            'px-4 py-2.5 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5',
            tab === 'manual'
              ? 'border-accent-purple text-accent-purple'
              : 'border-transparent text-text-muted hover:text-text-secondary'
          )}
        >
          Manual
          {oaImportedStrategies.length > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-accent-purple/15 text-accent-purple font-medium">
              {oaImportedStrategies.length}
            </span>
          )}
        </button>
        <button
          onClick={() => setTab('algo')}
          className={cn(
            'px-4 py-2.5 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5',
            tab === 'algo'
              ? 'border-accent-amber text-accent-amber'
              : 'border-transparent text-text-muted hover:text-text-secondary'
          )}
        >
          Algo Trades
        </button>
      </div>

      {/* Broker Positions tab — always mounted to keep refetchInterval alive */}
      <div className={tab === 'broker' ? 'contents' : 'hidden'}>
        <BrokerPositionsTable />
      </div>

      {/* Manual tab — OA Strategy Portfolio imported as Trademan strategies */}
      {tab === 'manual' && (
        <>
          {/* Toolbar */}
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-subtle bg-surface-1 shrink-0">
            {/* Sync status */}
            <div className="flex items-center gap-2">
              {oaFetching ? (
                <RefreshCw size={11} className="text-accent-blue animate-spin" />
              ) : oaError ? (
                <AlertCircle size={11} className="text-loss" />
              ) : (
                <CheckCircle2 size={11} className="text-profit" />
              )}
              <span className="text-xs text-text-muted">
                {oaError
                  ? 'OA unreachable'
                  : oaUpdatedAt
                    ? `Synced ${Math.round((Date.now() - oaUpdatedAt) / 1000)}s ago · auto every 30s`
                    : 'Auto-syncing every 30s'}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => invalidateOAPortfolio()}
                className="p-1.5 text-text-muted hover:text-text-primary transition-colors"
                title="Sync now"
              >
                <RefreshCw size={12} />
              </button>
              <button
                onClick={() => setOaBuilderOpen(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
              >
                <Plus size={12} />
                Add Strategy
              </button>
            </div>
          </div>

          {oaImportedStrategies.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full py-24 text-center">
              <Layers size={36} className="text-text-muted mb-3 opacity-40" />
              <p className="text-text-secondary text-sm mb-1">No manual strategies yet</p>
              {(oaError || oaSyncError) ? (
                <p className="text-loss text-xs mb-4">
                  Could not reach OpenAlgo — make sure it is running on port 5000.
                </p>
              ) : (
                <p className="text-text-muted text-xs mb-4">
                  Trademan auto-syncs every 30 s. Or click{' '}
                  <strong className="text-text-secondary">Add Strategy</strong> to open
                  the OpenAlgo builder and import immediately on close.
                </p>
              )}
              <button
                onClick={() => setOaBuilderOpen(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
              >
                <Plus size={12} />
                Add Strategy
              </button>
            </div>
          ) : (
            <>
              {/* Summary bar */}
              <div className="grid grid-cols-3 gap-3 p-4 border-b border-border-subtle bg-surface-1 shrink-0">
                <MetricCard
                  label="Total MTM"
                  value={fmtINRCompact(oaTotalMTM)}
                  valueClass={profitLossClass(oaTotalMTM)}
                  trend={oaTotalMTM > 0 ? 'up' : oaTotalMTM < 0 ? 'down' : null}
                  compact
                />
                <MetricCard label="Strategies" value={oaImportedStrategies.length} compact />
                <MetricCard
                  label="Active Legs"
                  value={oaImportedStrategies.reduce(
                    (s, st) => s + st.legs.filter((l) => l.status === 'FILLED').length, 0
                  )}
                  compact
                />
              </div>

              {/* Strategy rows — same component as Strategies tab: live LTP, P&L, alerts */}
              <div className="flex-1 overflow-y-auto custom-scroll">
                <table className="w-full border-collapse">
                  <tbody>
                    {oaImportedStrategies.map((strategy) => (
                      <StrategyRow
                        key={strategy.id}
                        strategy={strategy}
                        checkedLegs={checkedLegs}
                        onCheck={handleCheck}
                        onSelect={() => {}}
                        isSelected={false}
                        onRemove={handleRemove}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <OpenAlgoBuilderModal
            open={oaBuilderOpen}
            onOpenChange={setOaBuilderOpen}
            onDone={handleOADone}
          />
        </>
      )}

      {/* Algo Trades tab */}
      {tab === 'algo' && <AlgoTradesTab />}

      {/* Strategies tab */}
      {tab === 'strategies' && (
        <>
          {strategies.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full py-24 text-center">
              <Layers size={40} className="text-text-muted mb-4 opacity-40" />
              <p className="text-text-secondary text-sm mb-2">No active strategies</p>
              <p className="text-text-muted text-xs">Group broker positions to create a strategy.</p>
            </div>
          ) : (
            <>
              {/* Summary bar */}
              <div className="grid grid-cols-5 gap-3 p-4 border-b border-border-subtle bg-surface-1 shrink-0">
                <MetricCard
                  label="Total MTM"
                  value={fmtINRCompact(totalMTM)}
                  valueClass={profitLossClass(totalMTM)}
                  trend={totalMTM > 0 ? 'up' : totalMTM < 0 ? 'down' : null}
                  compact
                />
                <MetricCard label="Strategies" value={strategies.length} compact />
                <MetricCard label="Active Legs" value={strategies.reduce((s, st) => s + st.legs.filter((l) => l.status === 'FILLED').length, 0)} compact />
                <MetricCard
                  label="Alerts"
                  value={unreadCount}
                  valueClass={unreadCount > 0 ? 'text-loss' : 'text-text-muted'}
                  compact
                />
                <MetricCard
                  label="Peak MTM"
                  value={strategies.length ? fmtINRCompact(Math.max(...strategies.map((s) => s.peakProfit ?? s.liveMTM))) : '—'}
                  valueClass="text-profit"
                  compact
                />
              </div>

              {/* F&O Table */}
              <div className="flex-1 overflow-y-auto custom-scroll">
                {/* Section label */}
                <div className="px-4 pt-3 pb-1">
                  <span className="text-xs font-semibold text-text-muted uppercase tracking-wider">F&O</span>
                </div>
                <table className="w-full border-collapse">
                  <tbody>
                    {strategies.map((strategy) => (
                      <StrategyRow
                        key={strategy.id}
                        strategy={strategy}
                        checkedLegs={checkedLegs}
                        onCheck={handleCheck}
                        onSelect={() => {}}
                        isSelected={false}
                        onRemove={handleRemove}
                      />
                    ))}
                  </tbody>
                </table>
              </div>

            </>
          )}
        </>
      )}
    </div>
  )
}
