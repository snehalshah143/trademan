import axios from 'axios'

const BASE = '/api/v1/algo-trades'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface AlgoRun {
  id: number
  strategy_type: string      // CREDIT_SPREAD | DIRECTIONAL_FUTURES | ...
  strategy_id: string
  strategy_name: string
  underlying: string
  expiry: string
  lots: number | null
  lot_size: number | null
  entry_spot: number | null
  entry_time: string | null
  exit_time: string | null
  exit_reason: string | null
  realized_pnl: number | null
  status: 'OPEN' | 'CLOSED' | 'PAUSED'
  execution_mode: string
  trade_mode: 'MONITOR_ONLY' | 'ENTRY_EXEC_MONITOR' | 'EXIT_EXEC_MONITOR' | 'FULL_EXEC' | 'PAPER' | 'LIVE'
  notes: string | null
  created_at: string
  updated_at: string
  // Credit spread specific (nullable for other types)
  spread_type: string | null
  primary_strike: number | null
  hedge_strike: number | null
  net_premium: number | null
  max_profit: number | null
  max_loss: number | null
  // Directional futures specific (nullable for other types)
  direction: string | null
  // Summary fields from list query
  latest_mtm: number | null
  trade_count: number
  notif_count: number
  snapshot_count: number
}

export interface AlgoLeg {
  id: number
  run_id: number
  symbol: string
  exchange: string
  quantity: number
  product: string
  expiry: string | null
  entry_price: number | null
  exit_price: number | null
  entry_order_id: string | null
  exit_order_id: string | null
  entry_time: string | null
  exit_time: string | null
  status: string
  realized_pnl: number | null
  // Credit spread specific
  role: 'PRIMARY' | 'HEDGE' | null
  strike: number | null
  option_type: 'CE' | 'PE' | 'FUT' | null
  action: 'BUY' | 'SELL' | null
  // Directional futures specific
  direction: 'LONG' | 'SHORT' | null
  lot_size: number | null
}

export interface AlgoRunDetail extends AlgoRun {
  legs:              AlgoLeg[]
  // From latest MTM snapshot
  current_sl:        number | null   // current trailing SL price (locked_mtm)
  last_primary_ltp:  number | null
  last_hedge_ltp:    number | null
  last_futures_ltp:  number | null
}

export type TimelineKind = 'mtm' | 'trade' | 'signal' | 'adjustment' | 'notification'

export interface TimelineEvent {
  kind: TimelineKind
  id: number
  run_id: number
  ts?: string
  fill_time?: string
  created_at?: string
  [key: string]: unknown
}

export interface MtmPoint {
  ts: string
  spot: number
  mtm: number
  max_mtm: number | null
  locked_mtm: number | null
  primary_ltp: number | null
  hedge_ltp: number | null
  phase: string | null
}

// ── API ───────────────────────────────────────────────────────────────────────

export const algoTradesService = {
  async listRuns(params?: {
    status?: string
    underlying?: string
    date_from?: string
    date_to?: string
    include_open_all_time?: boolean
  }): Promise<AlgoRun[]> {
    const { data } = await axios.get(`${BASE}/runs`, { params })
    return data
  },

  async getRun(runId: number): Promise<AlgoRunDetail> {
    const { data } = await axios.get(`${BASE}/runs/${runId}`)
    return data
  },

  async getTimeline(runId: number, kind?: TimelineKind): Promise<TimelineEvent[]> {
    const { data } = await axios.get(`${BASE}/runs/${runId}/timeline`, {
      params: kind ? { kind } : undefined,
    })
    return data
  },

  async getMtm(runId: number): Promise<MtmPoint[]> {
    const { data } = await axios.get(`${BASE}/runs/${runId}/mtm`)
    return data
  },
}
