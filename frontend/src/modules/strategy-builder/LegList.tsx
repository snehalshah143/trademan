import { X } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { fmtPrice } from '@/lib/utils'
import type { StrategyLeg } from '@/types/domain'

interface LegListProps {
  legs: StrategyLeg[]
  onRemoveLeg: (legId: string) => void
}

export function LegList({ legs, onRemoveLeg }: LegListProps) {
  if (legs.length === 0) {
    return (
      <div className="text-center py-8 text-text-muted text-xs">
        No legs added yet — use the form above to add legs
      </div>
    )
  }

  return (
    <table className="trading-table">
      <thead>
        <tr>
          <th className="text-left">#</th>
          <th className="text-left">Side</th>
          <th className="text-left">Symbol</th>
          <th>Strike</th>
          <th>Expiry</th>
          <th>Qty</th>
          <th>Premium</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {legs.map((leg, i) => (
          <tr key={leg.id}>
            <td className="text-left text-text-muted">{i + 1}</td>
            <td className="text-left">
              <Badge variant={leg.side === 'BUY' ? 'buy' : 'sell'}>{leg.side}</Badge>
            </td>
            <td className="text-left">
              <div className="flex items-center gap-1.5">
                <Badge variant={leg.instrument.instrumentType === 'CE' ? 'buy' : leg.instrument.instrumentType === 'PE' ? 'sell' : 'default'}>
                  {leg.instrument.instrumentType}
                </Badge>
                <span className="text-text-secondary text-xs">{leg.instrument.symbol}</span>
              </div>
            </td>
            <td>{leg.instrument.strike ?? '—'}</td>
            <td>{leg.instrument.expiry ?? '—'}</td>
            <td>{leg.quantity}</td>
            <td>{leg.entryPrice != null ? fmtPrice(leg.entryPrice) : '—'}</td>
            <td>
              <button
                onClick={() => onRemoveLeg(leg.id)}
                className="text-text-muted hover:text-loss transition-colors"
              >
                <X size={13} />
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
