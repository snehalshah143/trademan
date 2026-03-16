import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Area,
  ReferenceLine,
  ReferenceDot,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts'
import type { PayoffData } from '@/types/domain'
import { fmtPrice, fmtINRCompact } from '@/lib/utils'

interface PayoffChartProps {
  payoffData: PayoffData
  height?: number
  showTodayCurve?: boolean
}

function formatYAxis(v: number) {
  if (Math.abs(v) >= 1000) return `${v > 0 ? '+' : ''}${fmtINRCompact(v)}`
  return `${v > 0 ? '+' : ''}${v.toFixed(0)}`
}

interface CustomTooltipProps {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string }>
  label?: number
  currentSpot: number
}

function CustomTooltip({ active, payload, label, currentSpot }: CustomTooltipProps) {
  if (!active || !payload?.length || label === undefined) return null

  const change = ((label - currentSpot) / currentSpot) * 100
  const changeStr = `${change >= 0 ? '+' : ''}${change.toFixed(1)}%`

  const today = payload.find((p) => p.name === 'livePnl')
  const expiry = payload.find((p) => p.name === 'theoreticalPnl')

  return (
    <div className="bg-[#1e293b] border border-[#334155] rounded-md shadow-lg px-3 py-2 text-xs">
      <div className="text-[#94a3b8] mb-1.5 font-medium">
        When price is at {fmtPrice(label, 0)} ({changeStr})
      </div>
      {today && (
        <div className="flex items-center gap-2">
          <div className="w-3 h-0.5 bg-[#3b82f6]" />
          <span className="text-[#94a3b8]">Today</span>
          <span className={`ml-auto font-mono font-medium ${today.value >= 0 ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}>
            {today.value >= 0 ? '+' : ''}{fmtINRCompact(today.value)}
          </span>
        </div>
      )}
      {expiry && (
        <div className="flex items-center gap-2 mt-0.5">
          <div className="w-3 h-0.5 bg-[#22c55e]" />
          <span className="text-[#94a3b8]">Expiry</span>
          <span className={`ml-auto font-mono font-medium ${expiry.value >= 0 ? 'text-[#22c55e]' : 'text-[#8b5cf6]'}`}>
            {expiry.value >= 0 ? '+' : ''}{fmtINRCompact(expiry.value)}
          </span>
        </div>
      )}
    </div>
  )
}

// Augment points with clamped values for split-color fills
function augmentPoints(points: PayoffData['points']) {
  return points.map((p) => ({
    ...p,
    expiryPos: Math.max(0, p.theoreticalPnl),
    expiryNeg: Math.min(0, p.theoreticalPnl),
  }))
}

export function PayoffChart({ payoffData, height = 240, showTodayCurve = true }: PayoffChartProps) {
  const { points, breakevens, currentSpot, currentMTM } = payoffData

  if (points.length === 0) {
    return (
      <div className="flex items-center justify-center text-text-muted text-xs" style={{ height }}>
        Add legs with entry prices to see payoff
      </div>
    )
  }

  const data = augmentPoints(points)

  // Find closest point to current spot for the MTM dot
  const closestPoint = points.reduce((best, p) =>
    Math.abs(p.spot - currentSpot) < Math.abs(best.spot - currentSpot) ? p : best
  , points[0])

  const spotLabel = `MTM: ${currentMTM >= 0 ? '+' : ''}${fmtINRCompact(currentMTM)}  |  Spot: ${fmtPrice(currentSpot, 0)}`

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 16, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e1e28" />

        <XAxis
          dataKey="spot"
          tickFormatter={(v) => fmtPrice(v, 0)}
          tick={{ fill: '#5a5a72', fontSize: 10 }}
          axisLine={{ stroke: '#2a2a36' }}
          tickLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tickFormatter={formatYAxis}
          tick={{ fill: '#5a5a72', fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          width={56}
        />

        <Tooltip
          content={<CustomTooltip currentSpot={currentSpot} />}
          cursor={{ stroke: '#3b82f6', strokeWidth: 1, strokeDasharray: '3 3' }}
        />

        {/* Expiry curve fills — green above zero, red below */}
        <Area
          type="monotone"
          dataKey="expiryPos"
          stroke="none"
          fill="rgba(34,197,94,0.10)"
          isAnimationActive={false}
          legendType="none"
        />
        <Area
          type="monotone"
          dataKey="expiryNeg"
          stroke="none"
          fill="rgba(239,68,68,0.10)"
          isAnimationActive={false}
          legendType="none"
        />

        {/* Today curve (blue solid) */}
        {showTodayCurve && (
          <Line
            type="monotone"
            dataKey="livePnl"
            stroke="#3b82f6"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 3, fill: '#3b82f6' }}
            isAnimationActive={false}
          />
        )}

        {/* Expiry curve (green/purple, dashed when today curve shown) */}
        <Line
          type="monotone"
          dataKey="theoreticalPnl"
          stroke="#22c55e"
          strokeWidth={showTodayCurve ? 1.5 : 2}
          strokeDasharray={showTodayCurve ? '5 3' : undefined}
          dot={false}
          activeDot={{ r: 3, fill: '#22c55e' }}
          isAnimationActive={false}
        />

        {/* Zero line */}
        <ReferenceLine y={0} stroke="#3a3a4a" strokeWidth={1} />

        {/* Current spot line with MTM label */}
        <ReferenceLine
          x={currentSpot}
          stroke="#f59e0b"
          strokeDasharray="4 3"
          strokeWidth={1.5}
          label={{
            value: spotLabel,
            position: 'insideTopRight',
            fill: '#f59e0b',
            fontSize: 9,
            offset: 4,
          }}
        />

        {/* Breakevens */}
        {breakevens.map((be, i) => (
          <ReferenceLine
            key={i}
            x={be}
            stroke="#ef4444"
            strokeDasharray="3 3"
            strokeWidth={1}
            label={{ value: 'BE', position: 'top', fill: '#ef4444', fontSize: 9 }}
          />
        ))}

        {/* MTM dot on today curve */}
        <ReferenceDot
          x={closestPoint.spot}
          y={currentMTM}
          r={4}
          fill="#22c55e"
          stroke="#111113"
          strokeWidth={1.5}
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
