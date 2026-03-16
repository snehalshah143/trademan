import { useState } from 'react'
import { TrendingUp } from 'lucide-react'
import { usePayoff } from '@hooks/usePayoff'
import { useGreeks } from '@hooks/useGreeks'
import { useLTPStore } from '@store/ltpStore'
import { PayoffChart } from '@/components/charts/PayoffChart'
import { MetricCard } from '@/components/ui/MetricCard'
import { computePOP, computeMarginApprox, computeRiskReward } from '@/lib/payoff'
import { fmtINRCompact, fmtPrice, profitLossClass, cn } from '@/lib/utils'
import type { Strategy } from '@/types/domain'

interface PayoffPanelProps {
  strategy: Strategy | null
  currentSpot?: number
}

type PanelTab = 'payoff' | 'greeks' | 'activity'

export function PayoffPanel({ strategy, currentSpot: externalSpot }: PayoffPanelProps) {
  const [activeTab, setActiveTab] = useState<PanelTab>('payoff')

  const ltpMap = useLTPStore((s) => s.ltpMap)
  const underlyingLTP = strategy
    ? (ltpMap[strategy.underlyingSymbol]?.tick.ltp ?? externalSpot ?? 22500)
    : (externalSpot ?? 22500)

  const payoffData = usePayoff(strategy, underlyingLTP)
  const { greeks, loading: greeksLoading } = useGreeks(strategy?.legs ?? [])

  const hasLegs = (strategy?.legs.length ?? 0) > 0

  // Derived metrics
  const pop = payoffData ? computePOP(payoffData.points) : 0
  const margin = strategy ? computeMarginApprox(strategy) : 0
  const riskReward = payoffData ? computeRiskReward(payoffData.maxProfit, payoffData.maxLoss) : 'NA'

  const currentMTM = payoffData?.currentMTM ?? 0
  const maxProfit = payoffData?.maxProfit ?? 0
  const maxLoss = payoffData?.maxLoss ?? 0
  const breakevens = payoffData?.breakevens ?? []

  const mtmPct = maxProfit > 0 ? (currentMTM / maxProfit) * 100 : 0

  const tabs: Array<{ key: PanelTab; label: string }> = [
    { key: 'payoff', label: 'Payoff' },
    { key: 'greeks', label: 'Greeks' },
    { key: 'activity', label: 'Activity' },
  ]

  return (
    <div className="flex flex-col h-full bg-surface-1 border-l border-border-subtle">
      {/* Tab bar */}
      <div className="flex items-center border-b border-border-subtle px-3 shrink-0">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={cn(
              'px-3 py-2.5 text-xs font-medium border-b-2 transition-colors',
              activeTab === tab.key
                ? 'border-accent-blue text-text-primary'
                : 'border-transparent text-text-muted hover:text-text-secondary'
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── PAYOFF TAB ── */}
      {activeTab === 'payoff' && (
        <div className="flex-1 overflow-y-auto custom-scroll p-3 space-y-3">
          {!hasLegs ? (
            <div className="flex flex-col items-center justify-center h-full py-16 text-center">
              <TrendingUp size={32} className="text-text-muted opacity-30 mb-3" />
              <p className="text-sm text-text-secondary">Analysis shows Payoff Graph, Statistics and more…</p>
              <p className="text-xs text-text-muted mt-1">
                Select trades from Option Chain or use a Prebuilt strategy
              </p>
            </div>
          ) : (
            <>
              {/* Metrics row 1 */}
              <div className="grid grid-cols-4 gap-2">
                <MetricCard
                  label="Total MTM"
                  value={`${currentMTM >= 0 ? '+' : ''}${fmtINRCompact(currentMTM)} (${mtmPct.toFixed(1)}%)`}
                  valueClass={profitLossClass(currentMTM)}
                  compact
                />
                <MetricCard
                  label="Max Profit"
                  value={maxProfit > 10_000_000 ? 'Unlimited' : fmtINRCompact(maxProfit)}
                  valueClass="text-profit"
                  compact
                />
                <MetricCard
                  label="Max Loss"
                  value={maxLoss < -10_000_000 ? 'Unlimited' : fmtINRCompact(maxLoss)}
                  valueClass="text-loss"
                  compact
                />
                <MetricCard
                  label="Margin Approx"
                  value={margin > 0 ? fmtINRCompact(margin) : '—'}
                  compact
                />
              </div>

              {/* Metrics row 2 */}
              <div className="flex items-center gap-4 px-1 py-1 bg-surface-2 rounded-md text-xs">
                <div className="flex items-center gap-1.5">
                  <span className="text-text-muted">POP</span>
                  <span className="font-mono text-text-primary font-medium">{pop.toFixed(1)}%</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-text-muted">Risk/Reward</span>
                  <span className="font-mono text-text-primary font-medium">{riskReward}</span>
                </div>
                <div className="flex items-center gap-1.5 flex-1 min-w-0">
                  <span className="text-text-muted shrink-0">Breakeven</span>
                  <span className="font-mono text-text-primary text-[11px] truncate">
                    {breakevens.length > 0
                      ? breakevens.map((be) => {
                          const pct = ((be - underlyingLTP) / underlyingLTP) * 100
                          return `${fmtPrice(be, 0)} (${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%)`
                        }).join('  ')
                      : '—'}
                  </span>
                </div>
              </div>

              {/* Greeks bar */}
              <div className="flex items-center gap-4 px-3 py-2 bg-surface-2 rounded-md border border-border-subtle text-xs">
                <div className="flex items-center gap-1.5">
                  <span className="text-text-muted">Delta</span>
                  <span className={cn('font-mono font-medium', profitLossClass(greeks.netDelta))}>
                    {greeks.netDelta.toFixed(2)}
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-text-muted">Gamma</span>
                  <span className="font-mono font-medium text-text-secondary">
                    {greeks.netGamma.toFixed(4)}
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-text-muted">Theta</span>
                  <span className={cn('font-mono font-medium', greeks.netTheta >= 0 ? 'text-profit' : 'text-loss')}>
                    {greeks.netTheta >= 0 ? '+' : ''}{fmtINRCompact(greeks.netTheta)}
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-text-muted">Vega</span>
                  <span className="font-mono font-medium text-text-secondary">
                    {fmtINRCompact(greeks.netVega)}
                  </span>
                </div>
              </div>

              {/* Chart */}
              <div className="relative">
                {payoffData ? (
                  <>
                    <PayoffChart payoffData={payoffData} height={280} showTodayCurve />
                    {/* Chart legend */}
                    <div className="flex items-center gap-4 mt-1 px-1">
                      <div className="flex items-center gap-1.5 text-[10px] text-text-muted">
                        <div className="w-4 h-0.5 bg-[#3b82f6]" />
                        <span>Today</span>
                      </div>
                      <div className="flex items-center gap-1.5 text-[10px] text-text-muted">
                        <div className="w-4 h-px bg-[#22c55e]" style={{ borderTop: '1px dashed #22c55e' }} />
                        <span>Expiry</span>
                      </div>
                      <div className="flex items-center gap-1.5 text-[10px] text-text-muted">
                        <div className="w-3 h-3 rounded-full bg-profit" />
                        <span>MTM</span>
                      </div>
                    </div>
                  </>
                ) : (
                  <div className="h-[280px] flex items-center justify-center text-text-muted text-xs">
                    Add entry prices to see payoff
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {/* ── GREEKS TAB ── */}
      {activeTab === 'greeks' && (
        <div className="flex-1 overflow-y-auto custom-scroll">
          {!hasLegs ? (
            <div className="flex items-center justify-center h-32 text-text-muted text-xs">
              Add legs to see Greeks
            </div>
          ) : (
            <table className="w-full text-xs">
              <thead className="border-b border-border-subtle">
                <tr className="text-[10px] text-text-muted uppercase">
                  <th className="px-3 py-2 text-left">Leg</th>
                  <th className="px-2 py-2 text-right">Delta</th>
                  <th className="px-2 py-2 text-right">Gamma</th>
                  <th className="px-2 py-2 text-right">Theta</th>
                  <th className="px-2 py-2 text-right">Vega</th>
                  <th className="px-2 py-2 text-right">IV</th>
                </tr>
              </thead>
              <tbody>
                {greeks.legs.map((lg) => {
                  const leg = strategy?.legs.find((l) => l.id === lg.legId)
                  if (!leg) return null
                  return (
                    <tr key={lg.legId} className="border-b border-border-subtle hover:bg-surface-2">
                      <td className="px-3 py-1.5 text-left text-text-secondary font-mono text-[11px]">
                        {leg.instrument.symbol}
                      </td>
                      <td className={cn('px-2 py-1.5 text-right font-mono', profitLossClass(lg.effectiveDelta))}>
                        {lg.effectiveDelta.toFixed(2)}
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono text-text-secondary">
                        {lg.gamma.toFixed(4)}
                      </td>
                      <td className={cn('px-2 py-1.5 text-right font-mono', lg.theta >= 0 ? 'text-profit' : 'text-loss')}>
                        {lg.theta.toFixed(2)}
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono text-text-secondary">
                        {lg.vega.toFixed(2)}
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono text-text-muted">
                        {lg.iv ? `${lg.iv.toFixed(1)}%` : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
              <tfoot className="border-t border-border-default bg-surface-2">
                <tr className="text-xs font-medium">
                  <td className="px-3 py-1.5 text-text-muted">Net</td>
                  <td className={cn('px-2 py-1.5 text-right font-mono', profitLossClass(greeks.netDelta))}>
                    {greeks.netDelta.toFixed(2)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-text-secondary">
                    {greeks.netGamma.toFixed(4)}
                  </td>
                  <td className={cn('px-2 py-1.5 text-right font-mono', greeks.netTheta >= 0 ? 'text-profit' : 'text-loss')}>
                    {greeks.netTheta.toFixed(2)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-text-secondary">
                    {greeks.netVega.toFixed(2)}
                  </td>
                  <td className="px-2 py-1.5 text-right"></td>
                </tr>
              </tfoot>
            </table>
          )}
          {greeksLoading && (
            <div className="text-center py-2 text-[10px] text-text-muted">Fetching Greeks…</div>
          )}
        </div>
      )}

      {/* ── ACTIVITY TAB ── */}
      {activeTab === 'activity' && (
        <div className="flex-1 flex items-center justify-center text-text-muted text-xs">
          Activity log coming soon
        </div>
      )}
    </div>
  )
}
