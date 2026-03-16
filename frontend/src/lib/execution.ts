import type { BrokerAdapter, PlaceOrderParams } from '@adapters/broker.adapter'
import type { Strategy, StrategyLeg, Order, ExecuteResult } from '@/types/domain'

// ─── Config ───────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 500
const FILL_TIMEOUT_MS  = 30_000

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function waitForFill(
  adapter: BrokerAdapter,
  orderId: string
): Promise<Order | null> {
  const deadline = Date.now() + FILL_TIMEOUT_MS

  while (Date.now() < deadline) {
    const order = await adapter.getOrderStatus(orderId)
    if (!order) {
      await sleep(POLL_INTERVAL_MS)
      continue
    }
    if (order.status === 'COMPLETE') return order
    if (order.status === 'REJECTED' || order.status === 'CANCELLED') return order
    await sleep(POLL_INTERVAL_MS)
  }

  return null // timeout
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function legToOrderParams(leg: StrategyLeg): PlaceOrderParams {
  return {
    symbol: leg.instrument.symbol,
    exchange: leg.instrument.exchange,
    side: leg.side,
    orderType: leg.orderType,
    productType: leg.productType,
    quantity: leg.quantity,
    price: leg.limitPrice,
  }
}

// ─── Entry Execution ──────────────────────────────────────────────────────────

/**
 * executeEntry
 *
 * ENTRY sequence (NEVER change):
 *   1. Place all BUY legs simultaneously
 *   2. Poll fills every 500ms, 30s timeout
 *   3. If any BUY rejected → abort, do NOT place SELL legs
 *   4. Place all SELL legs simultaneously
 */
export async function executeEntry(
  adapter: BrokerAdapter,
  strategy: Strategy,
  onLegUpdate: (legId: string, updates: Partial<StrategyLeg>) => void
): Promise<ExecuteResult> {
  const buyLegs  = strategy.legs.filter((l) => l.side === 'BUY')
  const sellLegs = strategy.legs.filter((l) => l.side === 'SELL')

  const filledLegs: string[]  = []
  const failedLegs: string[]  = []
  const allOrders: Order[]    = []

  // Step 1 — Place all BUY legs simultaneously
  const buyResults = await Promise.all(
    buyLegs.map(async (leg) => {
      const result = await adapter.placeOrder(legToOrderParams(leg))
      return { leg, result }
    })
  )

  // Step 2 — Poll for BUY fills
  for (const { leg, result } of buyResults) {
    if (!result.success || !result.orderId) {
      failedLegs.push(leg.id)
      onLegUpdate(leg.id, { status: 'ERROR', orderId: result.orderId })
      continue
    }

    onLegUpdate(leg.id, { status: 'PENDING', orderId: result.orderId })
    const order = await waitForFill(adapter, result.orderId)

    if (!order || order.status !== 'COMPLETE') {
      failedLegs.push(leg.id)
      onLegUpdate(leg.id, { status: 'ERROR' })
    } else {
      filledLegs.push(leg.id)
      allOrders.push(order)
      onLegUpdate(leg.id, { status: 'FILLED', entryPrice: order.avgPrice })
    }
  }

  // Step 3 — Abort if any BUY failed
  if (failedLegs.length > 0) {
    return {
      success: false,
      filledLegs,
      failedLegs,
      orders: allOrders,
      error: `${failedLegs.length} BUY leg(s) failed or rejected. SELL legs not placed.`,
    }
  }

  // Step 4 — Place all SELL legs simultaneously
  const sellResults = await Promise.all(
    sellLegs.map(async (leg) => {
      const result = await adapter.placeOrder(legToOrderParams(leg))
      return { leg, result }
    })
  )

  for (const { leg, result } of sellResults) {
    if (!result.success || !result.orderId) {
      failedLegs.push(leg.id)
      onLegUpdate(leg.id, { status: 'ERROR', orderId: result.orderId })
      continue
    }

    onLegUpdate(leg.id, { status: 'PENDING', orderId: result.orderId })
    const order = await waitForFill(adapter, result.orderId)

    if (!order || order.status !== 'COMPLETE') {
      failedLegs.push(leg.id)
      onLegUpdate(leg.id, { status: 'ERROR' })
    } else {
      filledLegs.push(leg.id)
      allOrders.push(order)
      onLegUpdate(leg.id, { status: 'FILLED', entryPrice: order.avgPrice })
    }
  }

  const success = failedLegs.length === 0
  return { success, filledLegs, failedLegs, orders: allOrders }
}

// ─── Exit Execution ───────────────────────────────────────────────────────────

/**
 * executeExit
 *
 * EXIT sequence (NEVER change):
 *   1. Place exit orders for all SELL legs simultaneously (buy back)
 *   2. Poll fills
 *   3. Place exit orders for all BUY legs simultaneously (sell out)
 */
export async function executeExit(
  adapter: BrokerAdapter,
  strategy: Strategy,
  onLegUpdate: (legId: string, updates: Partial<StrategyLeg>) => void,
  legIds?: string[]  // if provided, only exit these legs (partial exit)
): Promise<ExecuteResult> {
  const activeLeg = (leg: StrategyLeg) =>
    leg.status === 'FILLED' && (!legIds || legIds.includes(leg.id))

  // Exit SELL legs (buy back) first — critical for margin
  const sellLegsToExit = strategy.legs.filter((l) => l.side === 'SELL' && activeLeg(l))
  const buyLegsToExit  = strategy.legs.filter((l) => l.side === 'BUY'  && activeLeg(l))

  const filledLegs: string[] = []
  const failedLegs: string[] = []
  const allOrders: Order[]   = []

  // Step 1 — Exit SELL legs simultaneously (buy back)
  const sellExitResults = await Promise.all(
    sellLegsToExit.map(async (leg) => {
      const result = await adapter.placeOrder({
        ...legToOrderParams(leg),
        side: 'BUY', // reverse the original SELL
      })
      return { leg, result }
    })
  )

  // Step 2 — Poll SELL exit fills
  for (const { leg, result } of sellExitResults) {
    if (!result.success || !result.orderId) {
      failedLegs.push(leg.id)
      onLegUpdate(leg.id, { exitOrderId: result.orderId })
      continue
    }

    onLegUpdate(leg.id, { exitOrderId: result.orderId })
    const order = await waitForFill(adapter, result.orderId)

    if (!order || order.status !== 'COMPLETE') {
      failedLegs.push(leg.id)
    } else {
      filledLegs.push(leg.id)
      allOrders.push(order)
      onLegUpdate(leg.id, { status: 'EXITED', exitPrice: order.avgPrice })
    }
  }

  // Step 3 — Exit BUY legs simultaneously (sell out)
  const buyExitResults = await Promise.all(
    buyLegsToExit.map(async (leg) => {
      const result = await adapter.placeOrder({
        ...legToOrderParams(leg),
        side: 'SELL', // reverse the original BUY
      })
      return { leg, result }
    })
  )

  for (const { leg, result } of buyExitResults) {
    if (!result.success || !result.orderId) {
      failedLegs.push(leg.id)
      onLegUpdate(leg.id, { exitOrderId: result.orderId })
      continue
    }

    onLegUpdate(leg.id, { exitOrderId: result.orderId })
    const order = await waitForFill(adapter, result.orderId)

    if (!order || order.status !== 'COMPLETE') {
      failedLegs.push(leg.id)
    } else {
      filledLegs.push(leg.id)
      allOrders.push(order)
      onLegUpdate(leg.id, { status: 'EXITED', exitPrice: order.avgPrice })
    }
  }

  const success = failedLegs.length === 0
  return { success, filledLegs, failedLegs, orders: allOrders }
}
