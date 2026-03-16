import { useQuery } from '@tanstack/react-query'
import axios from 'axios'

export interface QuoteData {
  symbol:     string
  exchange:   string
  ltp:        number
  prev_close: number
  change:     number
  changePct:  number
  open:       number
  high:       number
  low:        number
}

/** True if current moment is within NSE market hours (Mon–Fri 09:15–15:30 IST). */
function isMarketOpen(): boolean {
  const now = new Date()
  const day = now.getDay() // 0 = Sun, 6 = Sat
  if (day === 0 || day === 6) return false
  // Convert local time to IST (UTC+5:30)
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60_000
  const ist = new Date(utcMs + 5.5 * 3_600_000)
  const minutes = ist.getHours() * 60 + ist.getMinutes()
  return minutes >= 9 * 60 + 15 && minutes < 15 * 60 + 30
}

/**
 * Fetch a live quote from the TradeMan backend proxy.
 * Polls every 5 s during market hours; fetches once outside market hours.
 */
export function useQuote(symbol: string, exchange: string, enabled = true) {
  return useQuery<QuoteData>({
    queryKey: ['quote', symbol, exchange],
    queryFn: () =>
      axios
        .get<QuoteData>('/api/quote', { params: { symbol, exchange } })
        .then((r) => r.data),
    enabled: enabled && !!symbol && !!exchange,
    refetchInterval: isMarketOpen() ? 5_000 : false,
    staleTime: 4_000,
    retry: 1,
    retryDelay: 2_000,
  })
}

const NSE_INDICES = new Set(['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'NIFTYNXT50', 'MIDCPNIFTY'])
const BSE_INDICES = new Set(['SENSEX', 'BANKEX'])

/** Returns the spot exchange string for a given F&O underlying. */
export function spotExchangeFor(underlying: string): string {
  if (BSE_INDICES.has(underlying)) return 'BSE_INDEX'
  if (NSE_INDICES.has(underlying)) return 'NSE_INDEX'
  return 'NSE'  // individual stocks trade on NSE
}
