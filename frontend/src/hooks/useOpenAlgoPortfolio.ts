import { useQuery, useQueryClient } from '@tanstack/react-query'

// Reads via Trademan backend (/api/v1/oa-portfolio) which queries OA's SQLite
// file directly — no CORS, no OA session needed.

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
      const res = await fetch('/api/v1/oa-portfolio')
      if (!res.ok) throw new Error(`oa-portfolio: ${res.status}`)
      const data = await res.json()
      if (data.status === 'unconfigured') throw new Error('OPENALGO_DB_PATH not set')
      return (data.items ?? []) as OAPortfolioEntry[]
    },
    staleTime: 15_000,
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    retry: false,
  })
}

export function useInvalidateOAPortfolio() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: ['oa-portfolio'] })
}
