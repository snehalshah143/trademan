import axios from 'axios'
import type { LTPTick, Order, OrderSide, OrderType, Position, ProductType } from '@/types/domain'

// ─── Abstract Broker Adapter ─────────────────────────────────────────────────

export interface PlaceOrderParams {
  symbol: string
  exchange: string
  side: OrderSide
  orderType: OrderType
  productType: ProductType
  quantity: number
  price?: number
  triggerPrice?: number
}

export interface PlaceOrderResult {
  success: boolean
  orderId?: string
  error?: string
}

export abstract class BrokerAdapter {
  abstract readonly name: string

  abstract getLTP(symbol: string, exchange: string): Promise<LTPTick>
  abstract getLTPBatch(symbols: Array<{ symbol: string; exchange: string }>): Promise<LTPTick[]>
  abstract subscribeWS(symbols: string[], onTick: (tick: LTPTick) => void): () => void
  abstract placeOrder(params: PlaceOrderParams): Promise<PlaceOrderResult>
  abstract cancelOrder(orderId: string): Promise<boolean>
  abstract getOrderStatus(orderId: string): Promise<Order | null>
  abstract getPositions(): Promise<Position[]>
  abstract getFunds(): Promise<{ available: number; used: number; total: number }>
}

// ─── OpenAlgo Adapter ────────────────────────────────────────────────────────

export class OpenAlgoAdapter extends BrokerAdapter {
  readonly name = 'openalgo'
  private apiKey: string
  private baseUrl: string
  private wsUrl: string

  constructor(config: { apiKey: string; host: string; wsHost: string }) {
    super()
    this.apiKey = config.apiKey
    this.baseUrl = `http://${config.host}`
    this.wsUrl = `ws://${config.wsHost}`
  }

  private get headers() {
    return {
      'Content-Type': 'application/json',
      'X-API-Key': this.apiKey,
    }
  }

  async getLTP(symbol: string, exchange: string): Promise<LTPTick> {
    const res = await axios.post(
      `${this.baseUrl}/api/v1/quotes`,
      { symbol, exchange },
      { headers: this.headers }
    )
    const d = res.data
    return {
      symbol,
      ltp: d.ltp,
      change: d.change ?? 0,
      changePct: d.change_percent ?? 0,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
      volume: d.volume,
      oi: d.oi,
      timestamp: Date.now(),
    }
  }

  async getLTPBatch(symbols: Array<{ symbol: string; exchange: string }>): Promise<LTPTick[]> {
    const results = await Promise.allSettled(
      symbols.map(({ symbol, exchange }) => this.getLTP(symbol, exchange))
    )
    return results
      .filter((r): r is PromiseFulfilledResult<LTPTick> => r.status === 'fulfilled')
      .map((r) => r.value)
  }

  subscribeWS(symbols: string[], onTick: (tick: LTPTick) => void): () => void {
    const ws = new WebSocket(`${this.wsUrl}/marketdata`)
    ws.onopen = () => {
      ws.send(JSON.stringify({ action: 'subscribe', symbols }))
    }
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.symbol && data.ltp !== undefined) {
          onTick({
            symbol: data.symbol,
            ltp: data.ltp,
            change: data.change ?? 0,
            changePct: data.change_percent ?? 0,
            timestamp: data.timestamp ?? Date.now(),
          })
        }
      } catch {
        // ignore parse errors
      }
    }
    return () => ws.close()
  }

  async placeOrder(params: PlaceOrderParams): Promise<PlaceOrderResult> {
    try {
      const res = await axios.post(
        `${this.baseUrl}/api/v1/placeorder`,
        {
          symbol: params.symbol,
          exchange: params.exchange,
          action: params.side,
          product: params.productType,
          pricetype: params.orderType,
          quantity: params.quantity,
          price: params.price ?? 0,
          trigger_price: params.triggerPrice ?? 0,
        },
        { headers: this.headers }
      )
      return { success: true, orderId: res.data.orderid }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      return { success: false, error: msg }
    }
  }

  async cancelOrder(orderId: string): Promise<boolean> {
    try {
      await axios.post(
        `${this.baseUrl}/api/v1/cancelorder`,
        { orderid: orderId },
        { headers: this.headers }
      )
      return true
    } catch {
      return false
    }
  }

  async getOrderStatus(orderId: string): Promise<Order | null> {
    try {
      const res = await axios.post(
        `${this.baseUrl}/api/v1/orderstatus`,
        { orderid: orderId },
        { headers: this.headers }
      )
      const d = res.data
      return {
        orderId: d.orderid,
        symbol: d.symbol,
        exchange: d.exchange,
        side: d.action,
        orderType: d.pricetype,
        productType: d.product,
        quantity: d.quantity,
        price: d.price,
        filledQuantity: d.filled_quantity ?? 0,
        avgPrice: d.average_price,
        status: d.status?.toUpperCase() ?? 'PENDING',
        placedAt: d.order_time ?? new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      }
    } catch {
      return null
    }
  }

  async getPositions(): Promise<Position[]> {
    const res = await axios.get(`${this.baseUrl}/api/v1/positions`, {
      headers: this.headers,
    })
    return (res.data ?? []).map((p: Record<string, unknown>) => ({
      symbol: p.symbol,
      exchange: p.exchange,
      productType: p.product,
      side: (p.quantity as number) >= 0 ? 'BUY' : 'SELL',
      quantity: Math.abs(p.quantity as number),
      avgPrice: p.average_price ?? 0,
      ltp: p.ltp ?? 0,
      pnl: p.pnl ?? 0,
      dayPnl: p.day_pnl ?? 0,
      realizedPnl: p.realized_pnl ?? 0,
      unrealizedPnl: p.unrealized_pnl ?? 0,
    }))
  }

  async getFunds(): Promise<{ available: number; used: number; total: number }> {
    const res = await axios.get(`${this.baseUrl}/api/v1/funds`, {
      headers: this.headers,
    })
    const d = res.data
    return {
      available: d.availablecash ?? 0,
      used: d.utilizedamount ?? 0,
      total: (d.availablecash ?? 0) + (d.utilizedamount ?? 0),
    }
  }
}

// ─── Mock Adapter ────────────────────────────────────────────────────────────

const MOCK_BASE_PRICES: Record<string, number> = {
  NIFTY: 22000,
  BANKNIFTY: 48000,
  FINNIFTY: 21500,
  MIDCPNIFTY: 10500,
  SENSEX: 73000,
}

function mockLTP(symbol: string): number {
  const base = MOCK_BASE_PRICES[symbol] ?? 100
  // Gaussian-ish drift within ±0.5%
  const drift = (Math.random() - 0.5) * 0.01 * base
  return parseFloat((base + drift).toFixed(2))
}

export class MockAdapter extends BrokerAdapter {
  readonly name = 'mock'
  private mockPrices: Record<string, number> = {}
  private wsIntervals: ReturnType<typeof setInterval>[] = []

  getLTP(symbol: string, _exchange: string): Promise<LTPTick> {
    const base = MOCK_BASE_PRICES[symbol] ?? 100
    const prev = this.mockPrices[symbol] ?? base
    const ltp = parseFloat((prev + (Math.random() - 0.5) * 0.005 * prev).toFixed(2))
    this.mockPrices[symbol] = ltp
    const close = base
    return Promise.resolve({
      symbol,
      ltp,
      change: parseFloat((ltp - close).toFixed(2)),
      changePct: parseFloat(((ltp - close) / close * 100).toFixed(2)),
      open: close,
      high: Math.max(close, ltp),
      low: Math.min(close, ltp),
      close,
      volume: Math.floor(Math.random() * 1_000_000),
      oi: Math.floor(Math.random() * 500_000),
      timestamp: Date.now(),
    })
  }

  async getLTPBatch(symbols: Array<{ symbol: string; exchange: string }>): Promise<LTPTick[]> {
    return Promise.all(symbols.map(({ symbol, exchange }) => this.getLTP(symbol, exchange)))
  }

  subscribeWS(symbols: string[], onTick: (tick: LTPTick) => void): () => void {
    // Simulate live ticks every 500ms per symbol
    for (const symbol of symbols) {
      if (!this.mockPrices[symbol]) {
        this.mockPrices[symbol] = MOCK_BASE_PRICES[symbol] ?? 100
      }
      const interval = setInterval(() => {
        const prev = this.mockPrices[symbol]
        const ltp = parseFloat((prev + (Math.random() - 0.5) * 0.002 * prev).toFixed(2))
        this.mockPrices[symbol] = ltp
        const base = MOCK_BASE_PRICES[symbol] ?? 100
        onTick({
          symbol,
          ltp,
          change: parseFloat((ltp - base).toFixed(2)),
          changePct: parseFloat(((ltp - base) / base * 100).toFixed(2)),
          timestamp: Date.now(),
        })
      }, 500)
      this.wsIntervals.push(interval)
    }
    return () => {
      this.wsIntervals.forEach(clearInterval)
      this.wsIntervals = []
    }
  }

  placeOrder(_params: PlaceOrderParams): Promise<PlaceOrderResult> {
    // Simulate 95% fill rate
    if (Math.random() < 0.05) {
      return Promise.resolve({ success: false, error: 'Mock rejection: insufficient funds' })
    }
    const orderId = `MOCK-${Date.now()}-${Math.random().toString(36).slice(2, 7).toUpperCase()}`
    return Promise.resolve({ success: true, orderId })
  }

  cancelOrder(_orderId: string): Promise<boolean> {
    return Promise.resolve(true)
  }

  async getOrderStatus(orderId: string): Promise<Order> {
    const ltp = mockLTP('NIFTY')
    return {
      orderId,
      symbol: 'NIFTY24MAR22000CE',
      exchange: 'NFO',
      side: 'BUY',
      orderType: 'MARKET',
      productType: 'NRML',
      quantity: 50,
      filledQuantity: 50,
      avgPrice: ltp,
      status: 'COMPLETE',
      placedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    }
  }

  async getPositions(): Promise<Position[]> {
    return []
  }

  async getFunds(): Promise<{ available: number; used: number; total: number }> {
    return { available: 500_000, used: 50_000, total: 550_000 }
  }
}
