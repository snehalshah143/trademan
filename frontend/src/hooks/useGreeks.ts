import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import axios from 'axios'
import type { StrategyLeg, Greeks, LegGreeks, StrategyGreeks } from '@/types/domain'

interface GreeksRequest {
  symbol: string
  strike?: number
  expiry?: string
  optionType?: string
  spotPrice?: number
  riskFreeRate?: number
}

interface GreeksResponse {
  delta: number
  gamma: number
  theta: number
  vega: number
  iv?: number
}

async function fetchGreeks(legs: StrategyLeg[]): Promise<Record<string, Greeks>> {
  const requests = legs
    .filter((l) => l.instrument.instrumentType === 'CE' || l.instrument.instrumentType === 'PE')
    .map((l): GreeksRequest & { legId: string } => ({
      legId: l.id,
      symbol: l.instrument.symbol,
      strike: l.instrument.strike,
      expiry: l.instrument.expiry,
      optionType: l.instrument.instrumentType,
    }))

  if (requests.length === 0) return {}

  try {
    const res = await axios.post<Array<{ legId: string } & GreeksResponse>>(
      '/api/instruments/greeks',
      { legs: requests }
    )
    const result: Record<string, Greeks> = {}
    for (const item of res.data) {
      result[item.legId] = {
        delta: item.delta,
        gamma: item.gamma,
        theta: item.theta,
        vega: item.vega,
        iv: item.iv,
      }
    }
    return result
  } catch {
    return {}
  }
}

// Approximate delta from option type (fallback when API unavailable)
function approximateDelta(leg: StrategyLeg): number {
  const { instrumentType } = leg.instrument
  if (instrumentType === 'CE') return 0.5
  if (instrumentType === 'PE') return -0.5
  if (instrumentType === 'FUT') return 1
  return 0
}

interface UseGreeksResult {
  greeks: StrategyGreeks
  loading: boolean
}

export function useGreeks(legs: StrategyLeg[]): UseGreeksResult {
  const cacheKey = legs.map((l) => `${l.id}:${l.instrument.symbol}`).join(',')

  const query = useQuery({
    queryKey: ['greeks', cacheKey],
    queryFn: () => fetchGreeks(legs),
    staleTime: 30_000,
    enabled: legs.length > 0,
  })

  const greeks = useMemo((): StrategyGreeks => {
    const greeksMap = query.data ?? {}

    const legGreeks: LegGreeks[] = legs.map((leg) => {
      const g = greeksMap[leg.id]
      const sideMult = leg.side === 'BUY' ? 1 : -1
      const delta = g?.delta ?? approximateDelta(leg)
      const effectiveDelta = sideMult * delta * leg.quantity

      return {
        legId: leg.id,
        delta: g?.delta ?? approximateDelta(leg),
        gamma: g?.gamma ?? 0,
        theta: g?.theta ?? 0,
        vega: g?.vega ?? 0,
        iv: g?.iv,
        effectiveDelta,
      }
    })

    return {
      netDelta: legGreeks.reduce((s, l) => s + l.effectiveDelta, 0),
      netGamma: legGreeks.reduce((s, l) => s + l.gamma * (l.effectiveDelta >= 0 ? 1 : -1) * Math.abs(l.effectiveDelta), 0),
      netTheta: legGreeks.reduce((s, l) => s + l.theta * (l.effectiveDelta >= 0 ? 1 : -1), 0),
      netVega:  legGreeks.reduce((s, l) => s + l.vega  * (l.effectiveDelta >= 0 ? 1 : -1), 0),
      legs: legGreeks,
    }
  }, [legs, query.data])

  return { greeks, loading: query.isFetching }
}
