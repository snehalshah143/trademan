import { useLTPStore } from '@store/ltpStore'
import { fmtPrice, fmtPct, fmtINRCompact, profitLossClass, cn } from '@/lib/utils'
import { Badge } from '@/components/ui/Badge'
import type { LiveLeg } from '@hooks/useLivePositions'

interface LegRowProps {
  leg: LiveLeg
  checked: boolean
  onCheck: (legId: string, checked: boolean) => void
  onExit: (legId: string) => void
}

export function LegRow({ leg, checked, onCheck, onExit }: LegRowProps) {
  const symbol = leg.instrument.symbol
  const entry = useLTPStore((s) => s.ltpMap[symbol])
  const direction = entry?.direction ?? 'flat'
  const flashKey = entry?.flashKey ?? 0

  const ltp = leg.currentLTP
  const entryPrice = leg.entryPrice ?? 0
  const changePct = entryPrice > 0 ? ((ltp - entryPrice) / entryPrice) * 100 : 0

  const flashClass =
    direction === 'up'   ? 'ltp-flash-up' :
    direction === 'down' ? 'ltp-flash-down' :
    ''

  const rowBg = leg.legMTM > 0 ? 'bg-green-500/5' : leg.legMTM < 0 ? 'bg-red-500/5' : ''

  return (
    <tr className={cn('border-b border-border-subtle hover:bg-surface-4 transition-colors', rowBg)}>
      <td className="px-3 py-1.5 text-left w-8">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onCheck(leg.id, e.target.checked)}
          className="accent-accent-blue"
        />
      </td>
      <td className="px-3 py-1.5 text-left">
        <div className="flex items-center gap-2">
          <Badge variant={leg.side === 'BUY' ? 'buy' : 'sell'}>{leg.side}</Badge>
          <span className="text-text-primary text-xs font-medium">{symbol}</span>
        </div>
      </td>
      <td className="px-3 py-1.5 text-right">
        <span className="num text-num-sm text-text-secondary">{leg.quantity}</span>
      </td>
      <td className="px-3 py-1.5 text-right">
        <span className="num text-num-sm text-text-secondary">{fmtPrice(entryPrice)}</span>
      </td>
      <td className="px-3 py-1.5 text-right">
        <span
          key={flashKey}
          className={cn('num text-num-sm text-text-primary inline-block', flashClass)}
        >
          {fmtPrice(ltp)}
        </span>
      </td>
      <td className="px-3 py-1.5 text-right">
        <span className={cn('num text-num-xs', profitLossClass(changePct))}>
          {fmtPct(changePct, 1)}
        </span>
      </td>
      <td className="px-3 py-1.5 text-right">
        <span className={cn('num text-num-sm font-medium', profitLossClass(leg.legMTM))}>
          {fmtINRCompact(leg.legMTM)}
        </span>
      </td>
      <td className="px-3 py-1.5 text-right">
        {leg.status === 'FILLED' && (
          <button
            onClick={() => onExit(leg.id)}
            className="text-xs px-2 py-0.5 bg-red-500/15 text-red-400 border border-red-500/25 rounded hover:bg-red-500/25 transition-colors"
          >
            Exit
          </button>
        )}
      </td>
    </tr>
  )
}
