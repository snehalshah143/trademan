import { X } from 'lucide-react'
import { useLTPStore } from '@store/ltpStore'
import { usePayoff } from '@hooks/usePayoff'
import { PayoffChart } from '@/components/charts/PayoffChart'
import { MetricCard } from '@/components/ui/MetricCard'
import { fmtINRCompact, fmtPrice, fmtPct, profitLossClass, cn } from '@/lib/utils'
import type { Strategy } from '@/types/domain'
import type { LiveStrategy } from '@hooks/useLivePositions'

interface PayoffDetailPanelProps {
  strategy: LiveStrategy
  onClose: () => void
  onExitStrategy: (strategy: Strategy) => void
}

export function PayoffDetailPanel({ strategy, onClose, onExitStrategy }: PayoffDetailPanelProps) {
  const niftyLTP = useLTPStore((s) => s.ltpMap[strategy.underlyingSymbol]?.tick.ltp ?? 22500)
  const payoffData = usePayoff(strategy, niftyLTP)

  return (
    <div className="border-t border-border-subtle bg-surface-1 animate-fade-in">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-subtle">
        <span className="font-medium text-sm text-text-primary">{strategy.name}</span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onExitStrategy(strategy)}
            className="px-3 py-1 text-xs font-medium bg-red-600 hover:bg-red-500 text-white rounded transition-colors"
          >
            Exit Strategy
          </button>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary">
            <X size={15} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 p-4">
        {/* Left: metrics + legs table */}
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-2">
            <MetricCard
              label="MTM"
              value={fmtINRCompact(strategy.liveMTM)}
              valueClass={profitLossClass(strategy.liveMTM)}
              compact
            />
            <MetricCard
              label="Max Profit"
              value={payoffData ? fmtINRCompact(payoffData.maxProfit) : '—'}
              valueClass="text-profit"
              compact
            />
            <MetricCard
              label="Max Loss"
              value={payoffData ? fmtINRCompact(payoffData.maxLoss) : '—'}
              valueClass="text-loss"
              compact
            />
            <MetricCard
              label="Spot"
              value={fmtPrice(niftyLTP)}
              valueClass="text-accent-amber"
              compact
            />
            <MetricCard
              label="Breakevens"
              value={payoffData?.breakevens.map((b) => fmtPrice(b, 0)).join(', ') || '—'}
              compact
            />
            <MetricCard
              label="MTM/MaxLoss"
              value={payoffData && payoffData.maxLoss < 0
                ? fmtPct((strategy.liveMTM / Math.abs(payoffData.maxLoss)) * 100, 1)
                : '—'}
              compact
            />
          </div>

          {/* Legs detail */}
          <table className="trading-table text-xs">
            <thead>
              <tr>
                <th className="text-left">Symbol</th>
                <th>Entry</th>
                <th>LTP</th>
                <th>Chg%</th>
                <th>MTM</th>
              </tr>
            </thead>
            <tbody>
              {strategy.legs.map((leg) => {
                const chgPct = leg.entryPrice ? ((leg.currentLTP - leg.entryPrice) / leg.entryPrice) * 100 : 0
                return (
                  <tr key={leg.id}>
                    <td className="text-left text-text-secondary">{leg.instrument.symbol}</td>
                    <td>{fmtPrice(leg.entryPrice ?? 0)}</td>
                    <td className="text-text-primary">{fmtPrice(leg.currentLTP)}</td>
                    <td className={profitLossClass(chgPct)}>{fmtPct(chgPct, 1)}</td>
                    <td className={cn('font-medium', profitLossClass(leg.legMTM))}>
                      {fmtINRCompact(leg.legMTM)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* Right: payoff chart */}
        <div>
          {payoffData ? (
            <>
              <PayoffChart payoffData={payoffData} height={240} />
              <div className="flex items-center gap-4 mt-2 px-1">
                <div className="flex items-center gap-1.5 text-xs text-text-muted">
                  <div className="w-5 h-px bg-text-muted opacity-50" style={{ borderTop: '1px dashed' }} />
                  <span>Entry</span>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-text-muted">
                  <div className="w-5 h-0.5 bg-accent-blue" />
                  <span>Live</span>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-text-muted">
                  <div className="w-2 h-2 rounded-full bg-profit" />
                  <span>MTM</span>
                </div>
              </div>
            </>
          ) : (
            <div className="h-60 flex items-center justify-center text-text-muted text-xs">
              No payoff data — add entry prices
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
