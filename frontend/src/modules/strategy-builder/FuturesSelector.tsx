import { useState, useMemo, useEffect } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { ChevronDown } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import axios from 'axios'
import { useLTPStore } from '@store/ltpStore'
import { fmtPrice, cn } from '@/lib/utils'
import { format, parseISO, differenceInCalendarDays, addMonths, lastDayOfMonth, getDay, subDays } from 'date-fns'
import type { StrategyLeg } from '@/types/domain'

interface FuturesSelectorProps {
  underlying: string
  legs: StrategyLeg[]
  selectedExpiry: string | null
  onSelectExpiry: (expiry: string) => void
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function daysTo(expiry: string): number {
  try {
    return Math.max(0, differenceInCalendarDays(parseISO(expiry), new Date()))
  } catch {
    return 0
  }
}

function formatExpiryLabel(expiry: string): string {
  try {
    return format(parseISO(expiry), 'dd MMM yy')
  } catch {
    return expiry
  }
}

/** Last Thursday of a given month */
function lastThursday(year: number, month: number): Date {
  const last = lastDayOfMonth(new Date(year, month, 1))
  const diff = (getDay(last) - 4 + 7) % 7
  return subDays(last, diff)
}

/** Fallback: compute next 3 monthly expiries (last Thursday of month) */
function computeMonthlyExpiries(): string[] {
  const expiries: string[] = []
  const now = new Date()
  for (let i = 0; i < 3; i++) {
    const target = addMonths(now, i)
    const thu = lastThursday(target.getFullYear(), target.getMonth())
    if (thu > now) {
      expiries.push(format(thu, 'yyyy-MM-dd'))
    }
  }
  return expiries
}

/** FUT symbol format: NIFTY26MARFUT */
function futSymbol(underlying: string, expiry: string): string {
  try {
    const d = parseISO(expiry)
    const yy = format(d, 'yy')
    const mon = format(d, 'MMM').toUpperCase()
    return `${underlying}${yy}${mon}FUT`
  } catch {
    return `${underlying}FUT`
  }
}

/** Synthetic futures price: Spot × (1 + 0.065 × days/365) */
function syntheticFut(spot: number, days: number): number {
  return spot * (1 + 0.065 * (days / 365))
}

// ─── Component ────────────────────────────────────────────────────────────────

export function FuturesSelector({ underlying, legs, selectedExpiry, onSelectExpiry }: FuturesSelectorProps) {
  const [open, setOpen] = useState(false)
  const ltpMap = useLTPStore((s) => s.ltpMap)
  const spot = ltpMap[underlying]?.tick.ltp ?? 0

  // Fetch monthly expiries from API (falls back to computed)
  const { data: apiExpiries } = useQuery({
    queryKey: ['futExpiries', underlying],
    queryFn: async () => {
      const res = await axios.get<{ expiries: string[] }>(
        `/api/instruments/expiries?symbol=${underlying}&exchange=NFO&type=FUT`
      )
      return res.data.expiries ?? []
    },
    staleTime: 60_000,
    retry: 0,
  })

  const expiries = useMemo(() => {
    const list = apiExpiries && apiExpiries.length > 0 ? apiExpiries : computeMonthlyExpiries()
    return list.slice(0, 3)
  }, [apiExpiries])

  const activeExpiry = selectedExpiry ?? expiries[0] ?? ''

  // Notify parent of the resolved active expiry when expiries first load
  useEffect(() => {
    if (!selectedExpiry && expiries.length > 0) {
      onSelectExpiry(expiries[0])
    }
  }, [expiries, selectedExpiry, onSelectExpiry])

  // FUT lots held per expiry
  function getFutLots(expiry: string): number {
    return legs
      .filter((l) => l.instrument.instrumentType === 'FUT' && l.instrument.expiry === expiry)
      .reduce((s, l) => s + l.lots, 0)
  }

  // Price display
  const activeSym = futSymbol(underlying, activeExpiry)
  const activeLTP = ltpMap[activeSym]?.tick.ltp
  const activeDays = daysTo(activeExpiry)
  const displayPrice = activeLTP ?? (spot > 0 ? syntheticFut(spot, activeDays) : null)

  if (expiries.length === 0) return null

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button className="flex items-center gap-1.5 px-2 py-1 rounded-md border border-border-subtle text-xs text-text-secondary hover:border-accent-blue hover:text-text-primary transition-colors">
          <span className="font-medium">
            FUT ({formatExpiryLabel(activeExpiry)})
          </span>
          {displayPrice && (
            <span className="font-mono text-text-primary">{fmtPrice(displayPrice)}</span>
          )}
          <ChevronDown size={11} className={cn('text-text-muted transition-transform', open && 'rotate-180')} />
        </button>
      </Popover.Trigger>

      <Popover.Portal>
        <Popover.Content
          className="w-[320px] bg-surface-1 border border-border-default rounded-lg shadow-modal z-50 overflow-hidden animate-fade-in"
          align="start"
          sideOffset={4}
        >
          <div className="px-3 py-2 border-b border-border-subtle">
            <span className="text-xs font-medium text-text-secondary">{underlying} Futures</span>
          </div>

          <table className="w-full text-xs">
            <thead>
              <tr className="text-[10px] text-text-muted uppercase border-b border-border-subtle">
                <th className="px-3 py-1.5 text-left">Expiry</th>
                <th className="px-3 py-1.5 text-right">LTP</th>
                <th className="px-3 py-1.5 text-right">Lots</th>
              </tr>
            </thead>
            <tbody>
              {expiries.map((exp) => {
                const isActive = exp === activeExpiry
                const sym = futSymbol(underlying, exp)
                const ltp = ltpMap[sym]?.tick.ltp
                const days = daysTo(exp)
                const price = ltp ?? (spot > 0 ? syntheticFut(spot, days) : null)
                const lots = getFutLots(exp)

                return (
                  <tr
                    key={exp}
                    onClick={() => { onSelectExpiry(exp); setOpen(false) }}
                    className={cn(
                      'border-b border-border-subtle cursor-pointer transition-colors',
                      isActive ? 'bg-[rgba(59,130,246,0.08)]' : 'hover:bg-surface-2'
                    )}
                  >
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2">
                        {/* Radio indicator */}
                        <div className={cn(
                          'w-3 h-3 rounded-full border-2 flex items-center justify-center shrink-0',
                          isActive ? 'border-accent-blue bg-accent-blue' : 'border-border-default'
                        )}>
                          {isActive && <div className="w-1.5 h-1.5 rounded-full bg-white" />}
                        </div>
                        <div>
                          <div className="text-text-primary font-medium">{formatExpiryLabel(exp)}</div>
                          <div className="text-text-muted text-[10px]">{days} days</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {price ? (
                        <div>
                          <div className="text-text-primary">{fmtPrice(price)}</div>
                          {!ltp && (
                            <div className="text-[10px] text-text-muted">synthetic</div>
                          )}
                        </div>
                      ) : '—'}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {lots > 0 ? (
                        <span className="text-accent-blue font-medium">{lots}</span>
                      ) : (
                        <span className="text-text-muted">—</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <div className="px-3 py-2 border-t border-border-subtle">
            <p className="text-[10px] text-text-muted">
              Selecting an expiry sets the reference price for margin estimation.
              To add a FUT leg, use the Leg Editor.
            </p>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}
