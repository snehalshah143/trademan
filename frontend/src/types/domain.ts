// ─── Enumerations ────────────────────────────────────────────────────────────

export type Exchange = 'NSE' | 'BSE' | 'NFO' | 'BFO' | 'MCX' | 'CDS'

export type InstrumentType = 'EQ' | 'FUT' | 'CE' | 'PE'

export type OrderSide = 'BUY' | 'SELL'

export type ProductType = 'NRML' | 'MIS' | 'CNC'

export type OrderType = 'MARKET' | 'LIMIT' | 'SL' | 'SL-M'

export type OrderStatus =
  | 'PENDING'
  | 'OPEN'
  | 'COMPLETE'
  | 'REJECTED'
  | 'CANCELLED'
  | 'TRIGGER_PENDING'

export type StrategyStatus =
  | 'DRAFT'
  | 'PENDING_ENTRY'
  | 'ACTIVE'
  | 'PENDING_EXIT'
  | 'CLOSED'
  | 'ERROR'

// ─── Instruments ─────────────────────────────────────────────────────────────

export interface Instrument {
  symbol: string
  exchange: Exchange
  instrumentType: InstrumentType
  expiry?: string       // ISO date string e.g. "2024-03-28"
  strike?: number
  lotSize: number
  tickSize: number
}

// ─── Strategy ────────────────────────────────────────────────────────────────

export interface StrategyLeg {
  id: string
  legIndex: number
  instrument: Instrument
  side: OrderSide
  lots: number          // number of lots
  quantity: number      // lots × lotSize
  productType: ProductType
  orderType: OrderType
  limitPrice?: number
  entryPrice?: number   // filled price at entry
  exitPrice?: number    // filled price at exit
  currentLTP?: number
  orderId?: string
  exitOrderId?: string
  status: 'DRAFT' | 'PENDING' | 'FILLED' | 'EXITED' | 'ERROR'
  isHedge: boolean      // true = SELL leg providing margin benefit
}

export interface Strategy {
  id: string
  name: string
  underlyingSymbol: string   // e.g. "NIFTY", "BANKNIFTY"
  underlyingExpiry?: string  // for futures-based underlyings
  legs: StrategyLeg[]
  status: StrategyStatus
  entryTime?: string         // ISO datetime
  exitTime?: string
  capitalDeployed?: number   // premium received/paid at entry
  currentMTM?: number        // live P&L
  peakProfit?: number
  peakLoss?: number
  notes?: string
  tags?: string[]
  alertRules?: AlertRules
  createdAt: string
  updatedAt: string
}

// ─── Orders ──────────────────────────────────────────────────────────────────

export interface Order {
  orderId: string
  strategyId?: string
  legId?: string
  symbol: string
  exchange: Exchange
  side: OrderSide
  orderType: OrderType
  productType: ProductType
  quantity: number
  price?: number
  triggerPrice?: number
  filledQuantity: number
  avgPrice?: number
  status: OrderStatus
  rejectionReason?: string
  placedAt: string      // ISO datetime
  updatedAt: string
}

// ─── Positions ───────────────────────────────────────────────────────────────

export interface Position {
  symbol: string
  exchange: Exchange
  productType: ProductType
  side: OrderSide
  quantity: number
  avgPrice: number
  ltp: number
  pnl: number
  dayPnl: number
  realizedPnl: number
  unrealizedPnl: number
}

// ─── Market Data ─────────────────────────────────────────────────────────────

export interface LTPTick {
  symbol: string
  ltp: number
  change: number        // absolute change from prev close
  changePct: number     // percentage change
  open?: number
  high?: number
  low?: number
  close?: number        // previous close
  volume?: number
  oi?: number           // open interest
  timestamp: number     // unix ms
}

// ─── Greeks ──────────────────────────────────────────────────────────────────

export interface Greeks {
  delta: number
  gamma: number
  theta: number
  vega: number
  iv?: number          // implied volatility %
  rho?: number
}

export interface LegGreeks extends Greeks {
  legId: string
  effectiveDelta: number  // delta × quantity × side_multiplier
}

export interface StrategyGreeks {
  netDelta: number
  netGamma: number
  netTheta: number
  netVega: number
  legs: LegGreeks[]
}

// ─── Payoff ──────────────────────────────────────────────────────────────────

export interface PayoffPoint {
  spot: number
  theoreticalPnl: number  // at entry prices (entry curve)
  livePnl: number         // at current LTPs (live curve)
}

export interface PayoffData {
  points: PayoffPoint[]
  breakevens: number[]
  maxProfit: number
  maxLoss: number
  currentMTM: number
  currentSpot: number
}

// ─── Alerts ──────────────────────────────────────────────────────────────────

export type AlertSeverity = 'INFO' | 'WARNING' | 'CRITICAL'

export type AlertTriggerType =
  | 'MTM_PROFIT_TARGET'
  | 'MTM_LOSS_LIMIT'
  | 'LEG_LTP_ABOVE'
  | 'LEG_LTP_BELOW'
  | 'SPOT_ABOVE'
  | 'SPOT_BELOW'
  | 'TIME_BASED'
  | 'CUSTOM'

export interface AlertRule {
  id: string
  type: AlertTriggerType
  severity: AlertSeverity
  threshold: number
  legId?: string       // for leg-specific alerts
  message?: string
  enabled: boolean
  triggered: boolean
  triggeredAt?: string
}

// ─── Rich alert rule types ────────────────────────────────────────────────────

export interface PositionLegAlert {
  legId: string
  targetPrice: number | null
  slPrice: number | null
}

export interface UnderlyingAlert {
  enabled: boolean
  operator: 'less_than' | 'greater_than' | 'equal_to'
  value: number
}

export interface DeltaAlert {
  enabled: boolean
  operator: 'less_than' | 'greater_than'
  value: number
}

export interface AlertRules {
  // Per-leg position alerts
  positionAlerts: PositionLegAlert[]

  // Overall MTM alerts
  overallTarget: { enabled: boolean; mtmValue: number } | null
  overallStopLoss: { enabled: boolean; mtmValue: number } | null

  // Underlying price alert
  underlyingAlert: UnderlyingAlert | null

  // Delta alert
  deltaAlert: DeltaAlert | null

  // Legacy named rules (kept for Position Manager backward compat)
  beProximityPct?: AlertRule
  mtmTarget?: AlertRule
  mtmStopLoss?: AlertRule
  maxLossPct?: AlertRule
  spotAbove?: AlertRule
  spotBelow?: AlertRule
}

export interface AlertEvent {
  id: string
  strategyId: string
  strategyName: string
  ruleId: string
  type: AlertTriggerType
  severity: AlertSeverity
  message: string
  threshold: number
  actualValue: number
  timestamp: string    // ISO datetime
  acknowledged: boolean
}

// ─── WebSocket ───────────────────────────────────────────────────────────────

export type WSMessageType =
  | 'LTP_TICK'
  | 'LTP_BATCH'
  | 'ALERT_FIRED'
  | 'CONNECTION_STATUS'
  | 'STALE_WARNING'
  | 'PING'
  | 'PONG'
  | 'ERROR'

export type WSConnectionStatus = 'CONNECTING' | 'CONNECTED' | 'DISCONNECTED' | 'RECONNECTING' | 'FAILED'

export interface WSMessage<T = unknown> {
  type: WSMessageType
  payload: T
  timestamp: number
}

export interface WSLTPTickPayload {
  symbol: string
  ltp: number
  change: number
  changePct: number
  timestamp: number
}

export interface WSLTPBatchPayload {
  ticks: WSLTPTickPayload[]
  timestamp: number
}

export interface WSConnectionStatusPayload {
  status: WSConnectionStatus
  message?: string
  reconnectAttempt?: number
}

// ─── Broker Config ───────────────────────────────────────────────────────────

export interface BrokerConfig {
  adapter: 'openalgo' | 'mock'
  openalgoHost?: string
  openalgoWsHost?: string
  apiKey?: string
  clientId?: string
}

// ─── Execution ───────────────────────────────────────────────────────────────

export interface ExecuteResult {
  success: boolean
  filledLegs: string[]    // leg IDs successfully filled
  failedLegs: string[]    // leg IDs that failed/rejected
  orders: Order[]
  error?: string
}

// ─── UI State ────────────────────────────────────────────────────────────────

export interface ToastData {
  id: string
  type: 'success' | 'error' | 'warning' | 'info'
  message: string
  duration?: number
}

export interface ModalState {
  isOpen: boolean
  title?: string
  content?: React.ReactNode
  onConfirm?: () => void
  onCancel?: () => void
}
