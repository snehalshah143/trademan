import axios from 'axios'

export interface BrokerPosition {
  symbol:      string
  exchange:    string
  qty:         number
  buy_avg:     number
  sell_avg:    number
  pnl:         number
  ltp:         number
  product:     string
  strategy_id: string | null
  leg_id:      string | null
}

export interface FundsSummary {
  available: number
  used:      number
  total:     number
}

export const positionService = {
  getPositions: () =>
    axios.get<BrokerPosition[]>('/api/v1/positions').then(r => r.data),

  getFunds: () =>
    axios.get<FundsSummary>('/api/v1/positions/funds').then(r => r.data),
}
