import { useQuery } from '@tanstack/react-query'

export interface LegGreeks {
  leg_id:       string
  symbol:       string
  action:       string
  option_type:  string
  delta:        number | null
  gamma:        number | null
  theta:        number | null
  vega:         number | null
  signed_delta: number | null
}

export interface StrategyGreeks {
  strategy_id: string
  legs:        LegGreeks[]
  net_delta:   number | null
  net_gamma:   number | null
  net_theta:   number | null
  net_vega:    number | null
}

export function useStrategyGreeks(strategyId: string | null | undefined) {
  return useQuery<StrategyGreeks>({
    queryKey: ['greeks', strategyId],
    queryFn: async () => {
      const res = await fetch(`/api/v1/greeks/${strategyId}`)
      if (!res.ok) throw new Error(`greeks: ${res.status}`)
      return res.json() as Promise<StrategyGreeks>
    },
    enabled:         !!strategyId,
    staleTime:       30_000,
    refetchInterval: 60_000,
    retry:           false,
  })
}
