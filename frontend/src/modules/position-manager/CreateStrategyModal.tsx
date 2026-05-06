import { useState, useMemo } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X, FolderPlus } from 'lucide-react'
import { useStrategyStore } from '@store/strategyStore'
import { parseSymbol, extractUnderlying } from '@/lib/symbolParser'
import { cn, profitLossClass } from '@/lib/utils'
import type { BrokerPosition } from '@/services/positionService'
import type { Strategy, StrategyLeg, Instrument } from '@/types/domain'

interface Props {
  open:         boolean
  onOpenChange: (open: boolean) => void
  positions:    BrokerPosition[]
  onCreated?:   () => void
}

function buildInstrument(p: BrokerPosition): Instrument {
  const parsed = parseSymbol(p.symbol, p.exchange)
  return {
    symbol:         p.symbol,
    exchange:       p.exchange as Instrument['exchange'],
    instrumentType: parsed.isEquity ? 'EQ' : parsed.optType === 'FUT' ? 'FUT' : parsed.optType === 'CE' ? 'CE' : 'PE',
    expiry:         parsed.expiry ?? undefined,
    strike:         parsed.strike ?? undefined,
    lotSize:        1,
    tickSize:       0.05,
  }
}

export function CreateStrategyModal({ open, onOpenChange, positions, onCreated }: Props) {
  const addStrategy = useStrategyStore((s) => s.addStrategy)

  // Build locked prefix: "CDSL 26MAY26" or "CDSL + BHEL 26MAY26"
  const prefix = useMemo(() => {
    if (!positions.length) return ''
    const underlyings = [...new Set(positions.map((p) => extractUnderlying(p.symbol)))]
    const expiries = [...new Set(
      positions.map((p) => parseSymbol(p.symbol, p.exchange).expiryShort).filter(Boolean)
    )]
    const expPart = expiries.length === 1 ? expiries[0] : expiries.join('/')
    return `${underlyings.join(' + ')}${expPart ? ' ' + expPart : ''}`
  }, [positions])

  const [customName, setCustomName] = useState('')

  const displayName = customName.trim()
    ? `${prefix} ${customName.trim()}`
    : prefix

  const totalPnl = positions.reduce((s, p) => s + p.pnl, 0)

  function handleCreate() {
    const now = new Date().toISOString()

    const legs: StrategyLeg[] = positions.map((p, i) => ({
      id:          crypto.randomUUID(),
      legIndex:    i,
      instrument:  buildInstrument(p),
      side:        p.qty > 0 ? 'BUY' : 'SELL',
      lots:        1,
      quantity:    Math.abs(p.qty),
      productType: p.product as StrategyLeg['productType'],
      orderType:   'MARKET',
      entryPrice:  p.qty > 0 ? p.buy_avg : p.sell_avg,
      currentLTP:  p.ltp,
      status:      'FILLED',
      isHedge:     p.qty < 0,
    }))

    const firstParsed = parseSymbol(positions[0].symbol, positions[0].exchange)

    const strategy: Strategy = {
      id:                crypto.randomUUID(),
      name:              displayName,
      underlyingSymbol:  firstParsed.underlying,
      underlyingExpiry:  firstParsed.expiry ?? undefined,
      legs,
      status:            'ACTIVE',
      entryTime:         now,
      currentMTM:        totalPnl,
      createdAt:         now,
      updatedAt:         now,
    }

    addStrategy(strategy)
    setCustomName('')
    onOpenChange(false)
    onCreated?.()
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[540px] max-w-[95vw] bg-surface-1 border border-border-default rounded-xl shadow-modal flex flex-col max-h-[80vh]">
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle">
            <div className="flex items-center gap-2">
              <FolderPlus size={15} className="text-accent-blue" />
              <Dialog.Title className="text-sm font-semibold text-text-primary">
                Group as Strategy
              </Dialog.Title>
            </div>
            <Dialog.Close className="text-text-muted hover:text-text-primary">
              <X size={15} />
            </Dialog.Close>
          </div>

          {/* Strategy name */}
          <div className="px-5 py-4 border-b border-border-subtle">
            <label className="flex flex-col gap-1.5">
              <span className="text-xs text-text-muted">Strategy Name</span>
              <div className="flex items-center border border-border-default rounded-md overflow-hidden focus-within:border-accent-blue transition-colors">
                {/* Locked prefix */}
                <span className="px-3 py-2 text-sm font-medium text-text-muted bg-surface-2 border-r border-border-default whitespace-nowrap shrink-0">
                  {prefix}
                </span>
                {/* User's custom name */}
                <input
                  className="flex-1 px-3 py-2 text-sm bg-transparent text-text-primary outline-none placeholder:text-text-muted"
                  placeholder="e.g. Debit Spread"
                  value={customName}
                  onChange={(e) => setCustomName(e.target.value)}
                  autoFocus
                />
              </div>
              {customName.trim() && (
                <span className="text-[11px] text-text-muted mt-1">
                  Will be saved as: <span className="text-text-secondary font-medium">{displayName}</span>
                </span>
              )}
            </label>
          </div>

          {/* Positions preview */}
          <div className="flex-1 overflow-y-auto custom-scroll">
            <div className="px-5 pt-3 pb-1">
              <span className="text-[11px] text-text-muted uppercase tracking-wider font-medium">
                {positions.length} Position{positions.length !== 1 ? 's' : ''} Selected
              </span>
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border-subtle">
                  <th className="px-5 py-2 text-left text-text-muted font-medium">Instrument</th>
                  <th className="px-3 py-2 text-left text-text-muted font-medium">Product</th>
                  <th className="px-3 py-2 text-right text-text-muted font-medium">Qty</th>
                  <th className="px-3 py-2 text-right text-text-muted font-medium">Avg Price</th>
                  <th className="px-3 py-2 text-right text-text-muted font-medium">LTP</th>
                  <th className="px-3 py-2 text-right text-text-muted font-medium">P&L</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => {
                  const parsed = parseSymbol(p.symbol, p.exchange)
                  const avg = p.qty > 0 ? p.buy_avg : p.sell_avg
                  return (
                    <tr key={i} className="border-b border-border-subtle hover:bg-surface-2">
                      <td className="px-5 py-2.5 text-text-primary font-medium">{parsed.displayName}</td>
                      <td className="px-3 py-2.5">
                        <span className="text-accent-amber text-[10px] font-semibold">{p.product}</span>
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <span className={cn(
                          'px-2 py-0.5 rounded text-[10px] font-semibold',
                          p.qty > 0 ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'
                        )}>
                          {p.qty > 0 ? `+${p.qty}` : p.qty}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right text-text-secondary">{avg.toFixed(2)}</td>
                      <td className="px-3 py-2.5 text-right text-text-primary">{p.ltp.toFixed(2)}</td>
                      <td className={cn('px-3 py-2.5 text-right font-medium', profitLossClass(p.pnl))}>
                        {p.pnl >= 0 ? '+' : ''}{p.pnl.toFixed(2)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Footer */}
          <div className="px-5 py-4 border-t border-border-subtle flex items-center justify-between">
            <div className="flex items-center gap-2 text-xs">
              <span className="text-text-muted">Net P&L:</span>
              <span className={cn('font-semibold', profitLossClass(totalPnl))}>
                {totalPnl >= 0 ? '+' : ''}₹{totalPnl.toFixed(2)}
              </span>
            </div>
            <div className="flex gap-2">
              <Dialog.Close className="px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary border border-border-default rounded-md">
                Cancel
              </Dialog.Close>
              <button
                onClick={handleCreate}
                disabled={positions.length === 0}
                className="px-4 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors disabled:opacity-40"
              >
                Create Strategy
              </button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
