import { useQuery } from '@tanstack/react-query'
import axios from 'axios'

// ─── Types ───────────────────────────────────────────────────────────────────

export interface OptionChainRow {
  strike:       number
  callLTP:      number
  putLTP:       number
  callBid:      number
  callAsk:      number
  putBid:       number
  putAsk:       number
  callDelta:    number
  putDelta:     number
  callIV:       number
  putIV:        number
  callOI:       number
  putOI:        number
  callOIChange: number
  putOIChange:  number
  callVolume:   number
  putVolume:    number
}

export interface OptionChainData {
  rows:          OptionChainRow[]
  atm_strike:    number | null
  spot:          number | null
  synthetic_fut: number | null
  expiry:        string
  error:         string | null
  error_code:    string | null    // 'master_contract' | 'unreachable' | null
}

export interface ExpiryInfo {
  expiry:         string
  display:        string
  days_to_expiry: number
  expiry_order:   number
}

interface ExpiryResponse {
  expiries:     string[]
  expiry_infos?: ExpiryInfo[]
  source?:      string
}

// ─── Fetchers ─────────────────────────────────────────────────────────────────

async function fetchExpiries(symbol: string, exchange: string): Promise<ExpiryResponse> {
  const res = await axios.get<ExpiryResponse>(
    `/api/instruments/expiries?symbol=${symbol}&exchange=${exchange}`
  )
  return res.data
}

async function fetchOptionChain(
  symbol: string,
  exchange: string,
  expiry: string,
  strikeCount: number,
): Promise<OptionChainData> {
  const res = await axios.get<OptionChainData>(
    `/api/instruments/optionchain?symbol=${symbol}&exchange=${exchange}&expiry=${expiry}&strike_count=${strikeCount}`
  )
  return res.data
}

// ─── Hook ────────────────────────────────────────────────────────────────────

interface UseOptionChainOptions {
  symbol:       string
  exchange?:    string
  expiry?:      string
  strikeCount?: number
  enabled?:     boolean
  polling?:     boolean
}

interface UseOptionChainResult {
  chain:           OptionChainData | undefined
  expiries:        string[]
  expiryInfos:     ExpiryInfo[]
  expiriesSource:  string
  loading:         boolean
  loadingExpiries: boolean
  error:           Error | null
}

export function useOptionChain({
  symbol,
  exchange = 'NFO',
  expiry,
  strikeCount = 10,
  enabled = true,
  polling = true,
}: UseOptionChainOptions): UseOptionChainResult {
  const expiriesQuery = useQuery({
    queryKey: ['expiries', symbol, exchange],
    queryFn: () => fetchExpiries(symbol, exchange),
    staleTime: 60_000,
    enabled: enabled && !!symbol,
  })

  const expiries      = expiriesQuery.data?.expiries ?? []
  const expiryInfos   = expiriesQuery.data?.expiry_infos ?? []
  const expiriesSource = expiriesQuery.data?.source ?? ''
  const activeExpiry  = expiry ?? expiries[0]

  const chainQuery = useQuery({
    queryKey: ['optionchain', symbol, exchange, activeExpiry, strikeCount],
    queryFn: () => fetchOptionChain(symbol, exchange, activeExpiry!, strikeCount),
    staleTime: 3_000,
    refetchInterval: polling ? 3_000 : false,
    enabled: enabled && !!symbol && !!activeExpiry,
  })

  return {
    chain:           chainQuery.data,
    expiries,
    expiryInfos,
    expiriesSource,
    loading:         chainQuery.isFetching,
    loadingExpiries: expiriesQuery.isFetching,
    error:           (chainQuery.error ?? expiriesQuery.error) as Error | null,
  }
}
