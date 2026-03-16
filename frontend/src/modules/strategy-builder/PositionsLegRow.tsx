import { useState, useRef } from 'react'
import { MoreHorizontal } from 'lucide-react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { useLTPStore } from '@store/ltpStore'
import { ToggleSwitch } from '@/components/ui/ToggleSwitch'
import { Badge } from '@/components/ui/Badge'
import { fmtPrice, fmtINRCompact, profitLossClass, cn } from '@/lib/utils'
import type { StrategyLeg } from '@/types/domain'
import { format, parseISO } from 'date-fns'

interface PositionsLegRowProps {
  leg: StrategyLeg
  enabled: boolean
  onToggle: (id: string, enabled: boolean) => void
  onEditLots: (id: string, lots: number) => void
  onToggleSide: (id: string) => void
  onRemove: (id: string) => void
  onEditEntryPrice: (id: string, price: number) => void
}

function formatExpiry(expiry?: string): string {
  if (!expiry) return ''
  try {
    return format(parseISO(expiry), 'dd MMM')
  } catch {
    return expiry.slice(0, 6)
  }
}

export function PositionsLegRow({
  leg,
  enabled,
  onToggle,
  onEditLots,
  onToggleSide,
  onRemove,
  onEditEntryPrice,
}: PositionsLegRowProps) {
  const ltpEntry = useLTPStore((s) => s.ltpMap[leg.instrument.symbol])
  const ltp = ltpEntry?.tick.ltp ?? leg.currentLTP ?? leg.entryPrice ?? 0
  const direction = ltpEntry?.direction ?? 'flat'
  const flashKey = ltpEntry?.flashKey ?? 0

  const [editingEntry, setEditingEntry] = useState(false)
  const [entryInput, setEntryInput] = useState(String(leg.entryPrice ?? ''))
  const entryRef = useRef<HTMLInputElement>(null)

  const entryPrice = leg.entryPrice ?? 0
  const sideMult = leg.side === 'BUY' ? 1 : -1
  const legMTM = enabled ? sideMult * (ltp - entryPrice) * leg.quantity : 0

  const expiry = formatExpiry(leg.instrument.expiry)
  const strike = leg.instrument.strike ?? ''
  const type = leg.instrument.instrumentType

  const identifier = leg.instrument.instrumentType === 'FUT'
    ? `${expiry}  FUT`
    : `${expiry}  ${strike}  ${type}  ×${leg.lots}`

  function commitEntryEdit() {
    const val = parseFloat(entryInput)
    if (!isNaN(val) && val > 0) {
      onEditEntryPrice(leg.id, val)
    }
    setEditingEntry(false)
  }

  const rowBg = !enabled
    ? 'opacity-40'
    : legMTM > 0
    ? 'bg-[rgba(34,197,94,0.04)]'
    : legMTM < 0
    ? 'bg-[rgba(239,68,68,0.04)]'
    : ''

  return (
    <tr className={cn('border-b border-border-subtle text-xs', rowBg)}>
      {/* Toggle */}
      <td className="px-2 py-2 w-8">
        <ToggleSwitch
          checked={enabled}
          onCheckedChange={(v) => onToggle(leg.id, v)}
          size="sm"
        />
      </td>

      {/* Side badge */}
      <td className="px-1 py-2 w-6">
        <Badge variant={leg.side === 'BUY' ? 'buy' : 'sell'}>
          {leg.side === 'BUY' ? 'B' : 'S'}
        </Badge>
      </td>

      {/* Identifier */}
      <td className="px-2 py-2 text-left text-text-secondary font-mono">
        {identifier}
      </td>

      {/* Entry price — click to edit */}
      <td className="px-2 py-2 text-right font-mono">
        {editingEntry ? (
          <input
            ref={entryRef}
            autoFocus
            value={entryInput}
            onChange={(e) => setEntryInput(e.target.value)}
            onBlur={commitEntryEdit}
            onKeyDown={(e) => { if (e.key === 'Enter') commitEntryEdit() }}
            className="w-16 text-right px-1 py-0.5 bg-surface-3 border border-accent-blue rounded text-text-primary focus:outline-none"
          />
        ) : (
          <button
            onClick={() => { setEntryInput(String(leg.entryPrice ?? '')); setEditingEntry(true) }}
            className="text-text-secondary hover:text-text-primary transition-colors"
            title="Click to edit entry price"
          >
            {entryPrice > 0 ? fmtPrice(entryPrice) : <span className="text-text-muted italic">—</span>}
          </button>
        )}
      </td>

      {/* LTP — with flash */}
      <td className="px-2 py-2 text-right font-mono">
        <span
          key={flashKey}
          className={cn(
            'inline-block',
            direction === 'up' && 'ltp-flash-up',
            direction === 'down' && 'ltp-flash-down'
          )}
        >
          {fmtPrice(ltp)}
        </span>
      </td>

      {/* MTM */}
      <td className={cn('px-2 py-2 text-right font-mono font-medium', profitLossClass(legMTM))}>
        {enabled ? fmtINRCompact(legMTM) : '—'}
      </td>

      {/* Actions */}
      <td className="px-2 py-2 text-right w-8">
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button className="text-text-muted hover:text-text-primary p-0.5">
              <MoreHorizontal size={13} />
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content
              className="bg-surface-2 border border-border-default rounded-md shadow-modal py-1 z-50 min-w-[140px]"
              side="left"
            >
              <DropdownMenu.Item
                className="px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3 hover:text-text-primary cursor-pointer outline-none"
                onSelect={() => {
                  const lots = parseInt(prompt('Lots:', String(leg.lots)) ?? '')
                  if (!isNaN(lots) && lots > 0) onEditLots(leg.id, lots)
                }}
              >
                Edit lots
              </DropdownMenu.Item>
              <DropdownMenu.Item
                className="px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3 hover:text-text-primary cursor-pointer outline-none"
                onSelect={() => onToggleSide(leg.id)}
              >
                Toggle BUY/SELL
              </DropdownMenu.Item>
              <DropdownMenu.Separator className="my-1 border-t border-border-subtle" />
              <DropdownMenu.Item
                className="px-3 py-1.5 text-xs text-loss hover:bg-surface-3 cursor-pointer outline-none"
                onSelect={() => onRemove(leg.id)}
              >
                Remove leg
              </DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </td>
    </tr>
  )
}
