import { MetricCard } from '@/components/ui/MetricCard'
import type { PayoffData } from '@/types/domain'

interface GreeksSummaryProps {
  payoffData: PayoffData | null
}

// Simplified Greeks approximation from payoff shape
function approximateGreeks(data: PayoffData) {
  const points = data.points
  if (points.length < 3) return { delta: 0, gamma: 0, theta: 0, vega: 0 }

  const mid = Math.floor(points.length / 2)
  const dSpot = points[mid + 1].spot - points[mid - 1].spot
  const dPnl  = points[mid + 1].livePnl - points[mid - 1].livePnl
  const delta  = dSpot > 0 ? dPnl / dSpot : 0

  const dPnlFwd = points[mid + 1].livePnl - points[mid].livePnl
  const dPnlBwd = points[mid].livePnl - points[mid - 1].livePnl
  const dS = points[1].spot - points[0].spot
  const gamma = dS > 0 ? (dPnlFwd - dPnlBwd) / (dS * dS) : 0

  return {
    delta: parseFloat(delta.toFixed(2)),
    gamma: parseFloat(gamma.toFixed(4)),
    theta: 0,   // requires time parameter not available here
    vega:  0,   // requires IV not available here
  }
}

export function GreeksSummary({ payoffData }: GreeksSummaryProps) {
  const greeks = payoffData ? approximateGreeks(payoffData) : null

  const deltaClass = greeks && greeks.delta > 0 ? 'text-profit' : greeks && greeks.delta < 0 ? 'text-loss' : 'text-text-muted'
  const gammaClass = greeks && greeks.gamma > 0 ? 'text-profit' : greeks && greeks.gamma < 0 ? 'text-loss' : 'text-text-muted'

  return (
    <div className="grid grid-cols-4 gap-2">
      <MetricCard
        label="Net Δ Delta"
        value={greeks ? greeks.delta.toFixed(2) : '—'}
        valueClass={deltaClass}
        compact
      />
      <MetricCard
        label="Net Γ Gamma"
        value={greeks ? greeks.gamma.toFixed(4) : '—'}
        valueClass={gammaClass}
        compact
      />
      <MetricCard
        label="Net Θ Theta"
        value="—"
        valueClass="text-text-muted"
        compact
      />
      <MetricCard
        label="Net V Vega"
        value="—"
        valueClass="text-text-muted"
        compact
      />
    </div>
  )
}
