import { useMemo } from 'react'
import { useStrategyStore } from '@store/strategyStore'
import { useLTPStore } from '@store/ltpStore'
import type { Strategy, StrategyLeg } from '@/types/domain'

export interface LiveLeg extends StrategyLeg {
  currentLTP: number
  legMTM: number
}

export interface LiveStrategy extends Strategy {
  legs: LiveLeg[]
  liveMTM: number
}

/**
 * Enriches active strategies with live MTM computed from the LTP store.
 * Re-renders whenever LTP map changes.
 */
export function useLivePositions(): LiveStrategy[] {
  // Select the raw array — Zustand+immer preserves the reference when unchanged,
  // so this won't trigger infinite re-renders (unlike calling getActiveStrategies()
  // which returns a new array reference on every selector invocation).
  const allStrategies = useStrategyStore((s) => s.strategies)
  const ltpMap = useLTPStore((s) => s.ltpMap)

  const activeStrategies = useMemo(
    () => allStrategies.filter((s) => s.status === 'ACTIVE'),
    [allStrategies]
  )

  return useMemo(() => {
    return activeStrategies.map((strategy) => {
      const enrichedLegs: LiveLeg[] = strategy.legs.map((leg) => {
        const symbol = leg.instrument.symbol
        const ltp = ltpMap[symbol]?.tick.ltp ?? leg.currentLTP ?? leg.entryPrice ?? 0
        const entryPrice = leg.entryPrice ?? 0
        const sideMult = leg.side === 'BUY' ? 1 : -1
        const legMTM = sideMult * (ltp - entryPrice) * leg.quantity

        return { ...leg, currentLTP: ltp, legMTM }
      })

      const liveMTM = enrichedLegs.reduce((sum, l) => sum + l.legMTM, 0)

      return { ...strategy, legs: enrichedLegs, liveMTM }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeStrategies, ltpMap])
}
