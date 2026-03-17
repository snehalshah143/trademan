import { useMemo } from 'react'
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
} from 'recharts'
import type { PayoffData } from '@/types/domain'
import { fmtPrice, fmtINRCompact } from '@/lib/utils'

export interface TodayPoint { spot: number; pnl: number }

interface PayoffChartProps {
  payoffData: PayoffData
  todayCurve?: TodayPoint[]
  height?: number
}

function fmtY(v: number) {
  const abs = Math.abs(v)
  if (abs >= 100000) return `${v > 0 ? '+' : ''}${(v / 100000).toFixed(1)}L`
  if (abs >= 1000) return `${v > 0 ? '+' : ''}${(v / 1000).toFixed(0)}K`
  return `${v > 0 ? '+' : ''}${v.toFixed(0)}`
}

interface TooltipProps {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string }>
  label?: number
  currentSpot: number
}

function CustomTooltip({ active, payload, label, currentSpot }: TooltipProps) {
  if (!active || !payload?.length || label === undefined) return null
  const change = ((label - currentSpot) / currentSpot) * 100
  const today = payload.find((p) => p.name === 'todayPnl')
  const expiry = payload.find((p) => p.name === 'theoreticalPnl')
  return (
    <div
      style={{
        background: '#18181c',
        border: '1px solid #2a2a36',
        borderRadius: 6,
        padding: '8px 12px',
        fontSize: 11,
        lineHeight: '1.6',
        boxShadow: '0 4px 16px rgba(0,0,0,0.6)',
      }}
    >
      <div style={{ color: '#5a5a72', fontWeight: 600, marginBottom: 4 }}>
        {fmtPrice(label, 0)}&nbsp;
        <span style={{ color: change >= 0 ? '#22c55e' : '#ef4444', fontWeight: 400 }}>
          ({change >= 0 ? '+' : ''}{change.toFixed(1)}%)
        </span>
      </div>
      {today && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 12, height: 2, background: '#3b82f6', borderRadius: 1 }} />
          <span style={{ color: '#9898b0' }}>Today</span>
          <span style={{ marginLeft: 'auto', fontFamily: 'monospace', fontWeight: 600, color: today.value >= 0 ? '#22c55e' : '#ef4444' }}>
            {today.value >= 0 ? '+' : ''}{fmtINRCompact(today.value)}
          </span>
        </div>
      )}
      {expiry && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 }}>
          <div style={{ width: 12, height: 2, background: '#22c55e', borderRadius: 1 }} />
          <span style={{ color: '#9898b0' }}>Expiry</span>
          <span style={{ marginLeft: 'auto', fontFamily: 'monospace', fontWeight: 600, color: expiry.value >= 0 ? '#22c55e' : '#ef4444' }}>
            {expiry.value >= 0 ? '+' : ''}{fmtINRCompact(expiry.value)}
          </span>
        </div>
      )}
    </div>
  )
}

export function PayoffChart({ payoffData, todayCurve, height = 240 }: PayoffChartProps) {
  const { points, breakevens, currentSpot, currentMTM } = payoffData

  const data = useMemo(() => {
    return points.map((p, i) => ({
      ...p,
      expiryPos: Math.max(0, p.theoreticalPnl),
      expiryNeg: Math.min(0, p.theoreticalPnl),
      todayPnl: todayCurve?.[i]?.pnl ?? p.livePnl,
    }))
  }, [points, todayCurve])

  if (points.length === 0) {
    return (
      <div
        style={{
          height,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#5a5a72',
          fontSize: 12,
        }}
      >
        Add legs with entry prices to see payoff
      </div>
    )
  }

  const closestIdx = points.reduce((bestIdx, p, i) =>
    Math.abs(p.spot - currentSpot) < Math.abs(points[bestIdx].spot - currentSpot) ? i : bestIdx
  , 0)
  const closestSpot = points[closestIdx].spot
  // MTM dot Y position: use today curve if available, else theoretical at that point
  const dotY = todayCurve?.[closestIdx]?.pnl ?? currentMTM

  return (
    <div
      style={{
        height,
        background: '#0a0a0b',
        borderRadius: 8,
        border: '1px solid #1e1e28',
        overflow: 'hidden',
      }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 14, right: 14, left: -4, bottom: 4 }}>

          <XAxis
            dataKey="spot"
            tickFormatter={(v) => fmtPrice(v, 0)}
            tick={{ fill: '#5a5a72', fontSize: 10 }}
            axisLine={{ stroke: '#1e1e28' }}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tickFormatter={fmtY}
            tick={{ fill: '#5a5a72', fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            width={50}
          />

          <Tooltip
            content={<CustomTooltip currentSpot={currentSpot} />}
            cursor={{ stroke: '#334155', strokeWidth: 1, strokeDasharray: '3 3' }}
          />

          {/* Profit zone — green fill above zero */}
          <Area
            type="monotone"
            dataKey="expiryPos"
            stroke="none"
            fill="rgba(34,197,94,0.15)"
            isAnimationActive={false}
            legendType="none"
          />
          {/* Loss zone — red fill below zero */}
          <Area
            type="monotone"
            dataKey="expiryNeg"
            stroke="none"
            fill="rgba(239,68,68,0.15)"
            isAnimationActive={false}
            legendType="none"
          />

          {/* Today's P&L — blue dashed curve (time value approx) */}
          <Line
            type="monotone"
            dataKey="todayPnl"
            stroke="#3b82f6"
            strokeWidth={2}
            strokeDasharray="6 3"
            dot={false}
            activeDot={{ r: 3, fill: '#3b82f6' }}
            isAnimationActive={false}
          />

          {/* At Expiry — solid green line */}
          <Line
            type="monotone"
            dataKey="theoreticalPnl"
            stroke="#22c55e"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 3, fill: '#22c55e' }}
            isAnimationActive={false}
          />

          {/* Zero line */}
          <ReferenceLine y={0} stroke="#2a2a36" strokeWidth={1} />

          {/* Current spot — amber vertical */}
          <ReferenceLine
            x={currentSpot}
            stroke="#f59e0b"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            label={{
              value: fmtPrice(currentSpot, 0),
              position: 'insideTopRight',
              fill: '#f59e0b',
              fontSize: 9,
              offset: 4,
            }}
          />

          {/* Breakeven lines */}
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

          {/* MTM dot at current spot */}
          <ReferenceDot
            x={closestSpot}
            y={dotY}
            r={4}
            fill="#22c55e"
            stroke="#0a0a0b"
            strokeWidth={1.5}
          />

        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
