import { TrendingUp, BookOpen, Bell, LogOut } from 'lucide-react'
import { useLTPStore } from '@store/ltpStore'
import { parseSymbol } from '@/lib/symbolParser'
import { fmtINRCompact, profitLossClass, cn } from '@/lib/utils'
import type { LiveLeg } from '@hooks/useLivePositions'

interface LegRowProps {
  leg:     LiveLeg
  onExit:  (legId: string) => void
}

function LotsPill({ value }: { value: number }) {
  return (
    <span className={cn(
      'inline-block px-2.5 py-0.5 rounded text-[11px] font-bold num min-w-[28px] text-center',
      value > 0 ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'
    )}>
      {value > 0 ? value : value}
    </span>
  )
}

function QtyPill({ value }: { value: number }) {
  return (
    <span className={cn(
      'inline-block px-2.5 py-0.5 rounded text-[11px] font-semibold num min-w-[44px] text-center',
      value > 0 ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'
    )}>
      {value}
    </span>
  )
}

export function LegRow({ leg, onExit }: LegRowProps) {
  const symbol  = leg.instrument.symbol
  const entry   = useLTPStore((s) => s.ltpMap[symbol])
  const flashKey = entry?.flashKey ?? 0
  const direction = entry?.direction ?? 'flat'

  const parsed     = parseSymbol(symbol, leg.instrument.exchange)
  const ltp        = leg.currentLTP
  const entryPrice = leg.entryPrice ?? 0
  const lotSize    = leg.instrument.lotSize > 0 ? leg.instrument.lotSize : 1

  // qty sign: positive for BUY, negative for SELL (to match image)
  const signedQty  = leg.side === 'BUY' ? leg.quantity : -leg.quantity
  const signedLots = leg.side === 'BUY'
    ? Math.round(leg.quantity / lotSize)
    : -Math.round(leg.quantity / lotSize)

  const flashClass =
    direction === 'up'   ? 'ltp-flash-up' :
    direction === 'down' ? 'ltp-flash-down' : ''

  const rowBg = leg.legMTM > 0 ? 'bg-green-500/4' : leg.legMTM < 0 ? 'bg-red-500/4' : ''

  return (
    <tr className={cn('border-b border-border-subtle hover:bg-surface-4 transition-colors', rowBg)}>
      {/* Action icons */}
      <td className="px-4 py-2.5">
        <div className="flex items-center gap-1.5">
          <button className="p-0.5 text-text-muted hover:text-accent-blue transition-colors" title="Payoff chart">
            <TrendingUp size={12} />
          </button>
          <button className="p-0.5 text-text-muted hover:text-accent-blue transition-colors" title="Order book">
            <BookOpen size={12} />
          </button>
          <button className="p-0.5 text-text-muted hover:text-accent-amber transition-colors" title="Alert">
            <Bell size={12} />
          </button>
        </div>
      </td>

      {/* Trade Instrument */}
      <td className="px-3 py-2.5 text-left">
        <span className="text-xs text-text-primary font-medium">{parsed.displayName}</span>
      </td>

      {/* Product Type */}
      <td className="px-3 py-2.5 text-center">
        <span className="text-[11px] font-semibold text-accent-amber">{leg.productType}</span>
      </td>

      {/* Lots */}
      <td className="px-3 py-2.5 text-center">
        <LotsPill value={signedLots} />
      </td>

      {/* Quantity */}
      <td className="px-3 py-2.5 text-center">
        <QtyPill value={signedQty} />
      </td>

      {/* Avg Price */}
      <td className="px-3 py-2.5 text-right">
        <span className="num text-xs text-text-secondary">{entryPrice > 0 ? entryPrice.toFixed(2) : '0'}</span>
      </td>

      {/* LTP */}
      <td className="px-3 py-2.5 text-right">
        <span key={flashKey} className={cn('num text-xs text-text-primary inline-block', flashClass)}>
          {ltp > 0 ? ltp.toFixed(2) : '—'}
        </span>
      </td>

      {/* P&L */}
      <td className="px-3 py-2.5 text-right">
        <span className={cn('num text-xs font-semibold', profitLossClass(leg.legMTM))}>
          {leg.legMTM >= 0 ? '+' : ''}{fmtINRCompact(leg.legMTM)}
        </span>
      </td>

      {/* Exit */}
      <td className="px-3 py-2.5 text-center">
        {leg.status === 'FILLED' && (
          <button
            onClick={() => onExit(leg.id)}
            className="p-1 text-text-muted hover:text-loss bg-loss/10 hover:bg-loss/20 rounded transition-colors"
            title="Exit leg"
          >
            <LogOut size={12} />
          </button>
        )}
      </td>
    </tr>
  )
}
