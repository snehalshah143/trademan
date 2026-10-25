import { useMemo } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useLTPStore } from '@store/ltpStore'
import { usePayoff } from '@hooks/usePayoff'
import { PayoffChart } from '@/components/charts/PayoffChart'
import { MetricCard } from '@/components/ui/MetricCard'
import { fmtINRCompact, fmtPrice } from '@/lib/utils'
import type { OAPortfolioEntry } from '@hooks/useOpenAlgoPortfolio'
import type { Strategy, StrategyLeg, Exchange, InstrumentType } from '@/types/domain'

function oaEntryToStrategy(entry: OAPortfolioEntry): Strategy {
  const now = new Date().toISOString()
  const legs: StrategyLeg[] = entry.legs.map((leg, i) => ({
    id: `oa-${entry.id}-${i}`,
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
    id: String(entry.id),
    name: entry.name,
    underlyingSymbol: entry.underlying,
    legs,
    status: 'ACTIVE',
    createdAt: entry.created_at ?? now,
    updatedAt: entry.updated_at ?? now,
  }
}

interface Props {
  entry: OAPortfolioEntry | null
  onClose: () => void
}

export function OAPayoffModal({ entry, onClose }: Props) {
  const ltpMap = useLTPStore((s) => s.ltpMap)

  const strategy = useMemo(
    () => (entry ? oaEntryToStrategy(entry) : null),
    [entry]
  )

  const currentSpot = entry
    ? (ltpMap[entry.underlying]?.tick.ltp ?? 22500)
    : 22500

  const payoffData = usePayoff(strategy, currentSpot)

  return (
    <Dialog.Root open={!!entry} onOpenChange={(open) => { if (!open) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[720px] max-w-[95vw] bg-surface-1 border border-border-default rounded-xl shadow-modal overflow-hidden flex flex-col">
          <Dialog.Title className="sr-only">Payoff — {entry?.name}</Dialog.Title>

          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-text-primary">{entry?.name}</span>
              <span className="text-xs text-text-muted px-1.5 py-0.5 bg-surface-3 rounded">
                {entry?.underlying}
              </span>
            </div>
            <button
              onClick={onClose}
              className="p-1.5 text-text-muted hover:text-text-primary rounded transition-colors"
            >
              <X size={15} />
            </button>
          </div>

          {/* Metrics */}
          <div className="grid grid-cols-4 gap-3 px-4 py-3 border-b border-border-subtle">
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
              label="Breakevens"
              value={payoffData?.breakevens.map((b) => fmtPrice(b, 0)).join(', ') || '—'}
              compact
            />
            <MetricCard
              label="Spot"
              value={fmtPrice(currentSpot, 0)}
              valueClass="text-accent-amber"
              compact
            />
          </div>

          {/* Chart */}
          <div className="p-4">
            {payoffData && payoffData.points.length > 0 ? (
              <>
                <PayoffChart payoffData={payoffData} height={300} />
                <div className="flex items-center gap-4 mt-2 px-1">
                  <div className="flex items-center gap-1.5 text-xs text-text-muted">
                    <div className="w-5 border-t-2 border-dashed border-accent-blue opacity-70" />
                    <span>Today</span>
                  </div>
                  <div className="flex items-center gap-1.5 text-xs text-text-muted">
                    <div className="w-5 h-0.5 bg-profit" />
                    <span>At Expiry</span>
                  </div>
                  <div className="flex items-center gap-1.5 text-xs text-text-muted">
                    <div className="w-1.5 h-1.5 rounded-full bg-profit" />
                    <span>MTM</span>
                  </div>
                </div>
              </>
            ) : (
              <div className="h-72 flex items-center justify-center text-text-muted text-xs">
                Unable to compute payoff — ensure legs have valid strike and option type
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
