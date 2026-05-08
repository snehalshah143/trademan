import { useQuery, useQueryClient } from '@tanstack/react-query'

const OA_BASE = 'http://127.0.0.1:5000'

export interface OAPortfolioLeg {
  segment: 'OPTION' | 'FUTURE'
  side: 'BUY' | 'SELL'
  lots: number
  lotSize: number
  expiry: string
  strike?: number
  optionType?: 'CE' | 'PE'
  price: number
  symbol: string
  exitPrice?: number
  active?: boolean
}

export interface OAPortfolioEntry {
  id: number
  watchlist: string
  name: string
  underlying: string
  exchange: string
  expiry: string | null
  legs: OAPortfolioLeg[]
  notes: string | null
  created_at: string | null
  updated_at: string | null
}

export function useOpenAlgoPortfolio() {
  return useQuery<OAPortfolioEntry[]>({
    queryKey: ['oa-portfolio'],
    queryFn: async () => {
      const res = await fetch(`${OA_BASE}/api/strategy-portfolio`, {
        credentials: 'include',
      })
      if (!res.ok) throw new Error(`OpenAlgo portfolio: ${res.status}`)
      const data = await res.json()
      return (data.items ?? []) as OAPortfolioEntry[]
    },
    staleTime: 15_000,
    refetchOnWindowFocus: true,
    retry: false,
  })
}

export function useInvalidateOAPortfolio() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: ['oa-portfolio'] })
}
