import { useMemo, useRef } from 'react'
import { useLTPStore } from '@store/ltpStore'
import { computePayoff } from '@/lib/payoff'
import type { Strategy, PayoffData } from '@/types/domain'

const RECOMPUTE_THRESHOLD = 0.001 // recompute if any LTP changes > 0.1%

function buildLtpMap(strategy: Strategy, ltpMap: Record<string, { tick: { ltp: number } }>): Record<string, number> {
  const result: Record<string, number> = {}
  for (const leg of strategy.legs) {
    const sym = leg.instrument.symbol
    result[sym] = ltpMap[sym]?.tick.ltp ?? leg.currentLTP ?? leg.entryPrice ?? 0
  }
  return result
}

/**
 * Computes payoff diagram for a strategy, memoised with 0.1% change threshold.
 */
export function usePayoff(strategy: Strategy | null, currentSpot: number): PayoffData | null {
  const ltpMap = useLTPStore((s) => s.ltpMap)

  const prevLtpRef = useRef<Record<string, number>>({})
  const prevPayoffRef = useRef<PayoffData | null>(null)

  return useMemo(() => {
    if (!strategy || strategy.legs.length === 0) return null

    const newLtp = buildLtpMap(strategy, ltpMap)

    // Check if any LTP changed more than threshold
    const anySignificantChange = Object.entries(newLtp).some(([sym, ltp]) => {
      const prev = prevLtpRef.current[sym]
      if (prev === undefined) return true
      return Math.abs(ltp - prev) / (prev || 1) > RECOMPUTE_THRESHOLD
    })

    if (!anySignificantChange && prevPayoffRef.current) {
      return prevPayoffRef.current
    }

    prevLtpRef.current = newLtp
    const result = computePayoff(strategy, currentSpot, newLtp)
    prevPayoffRef.current = result
    return result
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategy, currentSpot, ltpMap])
}
