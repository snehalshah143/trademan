import { useState } from 'react'
import { ChevronDown, ChevronRight, ExternalLink } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { OAPortfolioEntry, OAPortfolioLeg } from '@hooks/useOpenAlgoPortfolio'

function legDescriptor(leg: OAPortfolioLeg): string {
  if (leg.segment === 'FUTURE') return `${leg.symbol} FUT`
  return leg.symbol
}

interface Props {
  strategy: OAPortfolioEntry
}

export function OAStrategyCard({ strategy }: Props) {
  const [expanded, setExpanded] = useState(false)

  const legs = strategy.legs ?? []
  const buyCount  = legs.filter((l) => l.side === 'BUY').length
  const sellCount = legs.filter((l) => l.side === 'SELL').length

  // Net credit estimate (sell premium minus buy premium)
  const estCredit = legs.reduce((sum, l) => {
    const qty = l.lots * l.lotSize
    return sum + (l.side === 'SELL' ? 1 : -1) * l.price * qty
  }, 0)

  const createdLabel = strategy.created_at
    ? new Date(strategy.created_at).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })
    : null

  return (
    <tr className="border-b border-border-subtle">
      <td colSpan={9} className="px-0 py-0">
        {/* Collapsed header */}
        <div
          className="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-surface-2 transition-colors"
          onClick={() => setExpanded((p) => !p)}
        >
          <span className="text-text-muted shrink-0">
            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </span>

          <span className="text-xs font-semibold text-text-primary truncate flex-1 min-w-0">
            {strategy.name}
          </span>

          <span className="text-[10px] font-medium text-text-muted shrink-0">
            {strategy.underlying}
          </span>

          <div className="flex items-center gap-1 shrink-0">
            {buyCount > 0 && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-profit/10 text-profit">
                {buyCount}B
              </span>
            )}
            {sellCount > 0 && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-loss/10 text-loss">
                {sellCount}S
              </span>
            )}
          </div>

          <span className={cn('text-xs font-semibold num shrink-0', estCredit >= 0 ? 'text-profit' : 'text-loss')}>
            {estCredit >= 0 ? '+' : '-'}₹{Math.abs(estCredit).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
          </span>

          {createdLabel && (
            <span className="text-[10px] text-text-muted shrink-0">{createdLabel}</span>
          )}

          <a
            href={`http://127.0.0.1:5000/strategybuilder?portfolio=${strategy.id}`}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="p-1 text-text-muted hover:text-accent-blue transition-colors shrink-0"
            title="Open in OpenAlgo"
          >
            <ExternalLink size={12} />
          </a>
        </div>

        {/* Expanded legs */}
        {expanded && (
          <div className="px-10 pb-3 bg-surface-1/40">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-text-muted border-b border-border-subtle">
                  <th className="text-left py-1.5 font-semibold uppercase tracking-wider w-6">#</th>
                  <th className="text-left py-1.5 font-semibold uppercase tracking-wider">Symbol</th>
                  <th className="text-left py-1.5 font-semibold uppercase tracking-wider">Side</th>
                  <th className="text-right py-1.5 font-semibold uppercase tracking-wider">Lots</th>
                  <th className="text-right py-1.5 font-semibold uppercase tracking-wider">Entry Price</th>
                </tr>
              </thead>
              <tbody>
                {legs.map((leg, i) => (
                  <tr key={i} className="border-b border-border-subtle/30 last:border-0">
                    <td className="py-1.5 text-text-muted">{i + 1}</td>
                    <td className="py-1.5 font-mono text-text-primary">{legDescriptor(leg)}</td>
                    <td className="py-1.5">
                      <span className={cn(
                        'text-[10px] font-bold px-1.5 py-0.5 rounded',
                        leg.side === 'BUY'
                          ? 'bg-profit/10 text-profit'
                          : 'bg-loss/10 text-loss'
                      )}>
                        {leg.side}
                      </span>
                    </td>
                    <td className="py-1.5 text-right text-text-secondary num">{leg.lots}×</td>
                    <td className="py-1.5 text-right text-text-secondary num">₹{leg.price.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {strategy.notes && (
              <p className="mt-2 text-[11px] text-text-muted italic">{strategy.notes}</p>
            )}
          </div>
        )}
      </td>
    </tr>
  )
}
