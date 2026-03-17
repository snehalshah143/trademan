import type {
  Strategy,
  StrategyLeg,
  PayoffPoint,
  PayoffData,
  AlertRule,
} from '@/types/domain'

// ─── Constants ───────────────────────────────────────────────────────────────

const CALL_TYPES = new Set(['CE'])
const PUT_TYPES  = new Set(['PE'])

// ─── Option Intrinsic Value ───────────────────────────────────────────────────

function optionIntrinsic(
  leg: StrategyLeg,
  spot: number
): number {
  const { instrumentType } = leg.instrument
  if (CALL_TYPES.has(instrumentType)) {
    return Math.max(0, spot - (leg.instrument.strike ?? 0))
  }
  if (PUT_TYPES.has(instrumentType)) {
    return Math.max(0, (leg.instrument.strike ?? 0) - spot)
  }
  // Futures / EQ — use spot directly
  return spot
}

// ─── Leg P&L at a given spot ─────────────────────────────────────────────────

function legPnlAtSpot(
  leg: StrategyLeg,
  spot: number,
  useEntryPrice: boolean,
  ltpMap: Record<string, number> = {},
): number {
  const intrinsic = optionIntrinsic(leg, spot)
  const sym = leg.instrument.symbol
  // Use entry price if available; fall back to live LTP from map, then currentLTP, then 0
  const mapLtp = ltpMap[sym] ?? 0
  const refPrice = useEntryPrice
    ? (leg.entryPrice ?? mapLtp ?? leg.currentLTP ?? 0)
    : (mapLtp ?? leg.currentLTP ?? leg.entryPrice ?? 0)
  const sideMult = leg.side === 'BUY' ? 1 : -1
  return sideMult * (intrinsic - refPrice) * leg.quantity
}

// ─── Current MTM ─────────────────────────────────────────────────────────────

function computeCurrentMTM(legs: StrategyLeg[], ltpMap: Record<string, number>): number {
  return legs.reduce((total, leg) => {
    const ltp = ltpMap[leg.instrument.symbol] ?? leg.currentLTP ?? leg.entryPrice ?? 0
    const entryPrice = leg.entryPrice ?? 0
    const sideMult = leg.side === 'BUY' ? 1 : -1
    return total + sideMult * (ltp - entryPrice) * leg.quantity
  }, 0)
}

// ─── Main Payoff Computation ──────────────────────────────────────────────────

/**
 * computePayoff — pure O(1) per point, no network calls.
 *
 * @param strategy   The strategy with legs
 * @param currentSpot  Current underlying spot price
 * @param ltpMap     Map of symbol → current LTP for live curve
 * @param points     Number of points to compute (default 120)
 */
export function computePayoff(
  strategy: Strategy,
  currentSpot: number,
  ltpMap: Record<string, number>,
  points = 120
): PayoffData {
  // Include all non-error legs; legs without entryPrice use ltpMap or 0 as reference
  const legs = strategy.legs.filter((l) => l.status !== 'ERROR')

  // Only skip if there truly is no data at all (no strikes)
  const hasUsableLegs = legs.some(
    (l) => l.instrument.strike != null || l.instrument.instrumentType === 'FUT'
  )

  if (legs.length === 0 || !hasUsableLegs) {
    return {
      points: [],
      breakevens: [],
      maxProfit: 0,
      maxLoss: 0,
      currentMTM: 0,
      currentSpot,
    }
  }

  // Determine spot range: ±8% around current spot
  const rangePct = 0.08
  const spotMin = currentSpot * (1 - rangePct)
  const spotMax = currentSpot * (1 + rangePct)
  const step = (spotMax - spotMin) / (points - 1)

  const payoffPoints: PayoffPoint[] = []

  for (let i = 0; i < points; i++) {
    const spot = spotMin + step * i
    const theoreticalPnl = legs.reduce((sum, leg) => sum + legPnlAtSpot(leg, spot, true, ltpMap), 0)
    const livePnl = legs.reduce((sum, leg) => sum + legPnlAtSpot(leg, spot, false, ltpMap), 0)
    payoffPoints.push({ spot: parseFloat(spot.toFixed(2)), theoreticalPnl, livePnl })
  }

  // Breakevens — zero crossings on theoretical curve
  const breakevens: number[] = []
  for (let i = 1; i < payoffPoints.length; i++) {
    const prev = payoffPoints[i - 1]
    const curr = payoffPoints[i]
    if (prev.theoreticalPnl * curr.theoreticalPnl < 0) {
      // Linear interpolation
      const be =
        prev.spot +
        (0 - prev.theoreticalPnl) *
          ((curr.spot - prev.spot) / (curr.theoreticalPnl - prev.theoreticalPnl))
      breakevens.push(parseFloat(be.toFixed(2)))
    }
  }

  const pnls = payoffPoints.map((p) => p.theoreticalPnl)
  const maxProfit = Math.max(...pnls)
  const maxLoss = Math.min(...pnls)
  const currentMTM = computeCurrentMTM(legs, ltpMap)

  return {
    points: payoffPoints,
    breakevens,
    maxProfit,
    maxLoss,
    currentMTM,
    currentSpot,
  }
}

// ─── Today's Curve (Time Decay Approximation) ────────────────────────────────

/**
 * Approximates P&L if closed today at each spot, accounting for remaining time value.
 * Uses linear interpolation between current premium (full time value) and intrinsic (zero time).
 */
export function computeTodaysCurve(
  strategy: Strategy,
  currentSpot: number,
  ltpMap: Record<string, number>,
  daysToExpiry: number,
  points = 120
): Array<{ spot: number; pnl: number }> {
  const legs = strategy.legs.filter(
    (l) => l.status !== 'ERROR' && l.entryPrice !== undefined
  )
  if (legs.length === 0) return []

  const rangePct = 0.08
  const spotMin = currentSpot * (1 - rangePct)
  const spotMax = currentSpot * (1 + rangePct)
  const step = (spotMax - spotMin) / (points - 1)
  const totalDays = 30

  const result: Array<{ spot: number; pnl: number }> = []

  for (let i = 0; i < points; i++) {
    const spot = spotMin + step * i
    let pnl = 0

    for (const leg of legs) {
      const { instrumentType } = leg.instrument
      let intrinsic: number
      if (instrumentType === 'CE') {
        intrinsic = Math.max(0, spot - (leg.instrument.strike ?? 0))
      } else if (instrumentType === 'PE') {
        intrinsic = Math.max(0, (leg.instrument.strike ?? 0) - spot)
      } else {
        intrinsic = spot
      }

      const entryPremium = leg.entryPrice ?? 0
      const currentPremium = ltpMap[leg.instrument.symbol] ?? leg.currentLTP ?? entryPremium
      const sideMult = leg.side === 'BUY' ? 1 : -1

      let todayValue: number
      if (daysToExpiry > 0) {
        const timeRatio = Math.min(1, daysToExpiry / totalDays)
        todayValue = intrinsic * (1 - timeRatio) + currentPremium * timeRatio
      } else {
        todayValue = intrinsic
      }

      pnl += sideMult * (todayValue - entryPremium) * leg.quantity
    }

    result.push({ spot: parseFloat(spot.toFixed(2)), pnl })
  }

  return result
}

// ─── Derived Metrics ──────────────────────────────────────────────────────────

/** % of expiry range where P&L is positive (probability of profit approximation) */
export function computePOP(payoffPoints: PayoffPoint[]): number {
  if (payoffPoints.length === 0) return 0
  const profitable = payoffPoints.filter((p) => p.theoreticalPnl > 0).length
  return (profitable / payoffPoints.length) * 100
}

/** Rough SPAN margin approximation: 20% of notional for all SELL legs */
export function computeMarginApprox(strategy: Strategy): number {
  return strategy.legs
    .filter((l) => l.side === 'SELL')
    .reduce((sum, leg) => {
      const strike = leg.instrument.strike ?? 0
      return sum + strike * leg.quantity * 0.20
    }, 0)
}

/** Risk:Reward ratio string, or "NA" if either side is unlimited/zero */
export function computeRiskReward(maxProfit: number, maxLoss: number): string {
  if (maxLoss >= 0 || maxProfit <= 0) return 'NA'
  if (maxProfit > 10_000_000 || Math.abs(maxLoss) > 10_000_000) return 'NA'
  return (maxProfit / Math.abs(maxLoss)).toFixed(1)
}

// ─── Alert Rule Evaluation ───────────────────────────────────────────────────

export interface AlertCheckResult {
  ruleId: string
  triggered: boolean
  actualValue: number
}

export function checkAlertRules(
  rules: AlertRule[],
  payoffData: PayoffData,
  ltpMap: Record<string, number>
): AlertCheckResult[] {
  return rules
    .filter((r) => r.enabled && !r.triggered)
    .map((rule) => {
      let actualValue = 0
      let triggered = false

      switch (rule.type) {
        case 'MTM_PROFIT_TARGET':
          actualValue = payoffData.currentMTM
          triggered = actualValue >= rule.threshold
          break

        case 'MTM_LOSS_LIMIT':
          actualValue = payoffData.currentMTM
          triggered = actualValue <= -Math.abs(rule.threshold)
          break

        case 'SPOT_ABOVE':
          actualValue = payoffData.currentSpot
          triggered = actualValue > rule.threshold
          break

        case 'SPOT_BELOW':
          actualValue = payoffData.currentSpot
          triggered = actualValue < rule.threshold
          break

        case 'LEG_LTP_ABOVE':
          if (rule.legId) {
            actualValue = ltpMap[rule.legId] ?? 0
            triggered = actualValue > rule.threshold
          }
          break

        case 'LEG_LTP_BELOW':
          if (rule.legId) {
            actualValue = ltpMap[rule.legId] ?? 0
            triggered = actualValue < rule.threshold
          }
          break

        default:
          break
      }

      return { ruleId: rule.id, triggered, actualValue }
    })
}
