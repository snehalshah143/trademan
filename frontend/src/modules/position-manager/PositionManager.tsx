import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Layers } from 'lucide-react'
import axios from 'axios'
import { useLivePositions } from '@hooks/useLivePositions'
import { useBrokerPositions } from '@hooks/useBrokerPositions'
import { useAlertStore } from '@store/alertStore'
import { useLTPStore } from '@store/ltpStore'
import { useStrategyStore } from '@store/strategyStore'
import { MetricCard } from '@/components/ui/MetricCard'
import { StrategyRow } from './StrategyRow'
import { BrokerPositionsTable } from './BrokerPositionsTable'
import { fmtINRCompact, profitLossClass, cn } from '@/lib/utils'

type Tab = 'strategies' | 'broker'

export function PositionManager() {
  const strategies  = useLivePositions()
  const unreadCount = useAlertStore((s) => s.unreadCount)
  const updateBatch = useLTPStore((s) => s.updateBatch)
  const allStrategies = useStrategyStore((s) => s.strategies)

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

  const [tab,         setTab]         = useState<Tab>('broker')
  const [checkedLegs, setCheckedLegs] = useState<Set<string>>(new Set())

  const totalMTM = strategies.reduce((s, st) => s + st.liveMTM, 0)

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
      </div>

      {/* Broker Positions tab — always mounted to keep refetchInterval alive */}
      <div className={tab === 'broker' ? 'contents' : 'hidden'}>
        <BrokerPositionsTable />
      </div>

      {/* Strategies tab */}
      {tab === 'strategies' && (
        <>
          {strategies.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full py-24 text-center">
              <Layers size={40} className="text-text-muted mb-4 opacity-40" />
              <p className="text-text-secondary text-sm mb-2">No active strategies</p>
              <p className="text-text-muted text-xs mb-6">Go to Strategy Builder to create one</p>
              <Link
                to="/builder"
                className="px-4 py-2 text-sm font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
              >
                Open Strategy Builder
              </Link>
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
