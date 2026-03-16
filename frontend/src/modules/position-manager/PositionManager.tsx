import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Layers } from 'lucide-react'
import { useLivePositions } from '@hooks/useLivePositions'
import { useAlertStore } from '@store/alertStore'
import { MetricCard } from '@/components/ui/MetricCard'
import { StrategyRow } from './StrategyRow'
import { PayoffDetailPanel } from './PayoffDetailPanel'
import { ExitConfirmModal } from './ExitConfirmModal'
import { fmtINRCompact, profitLossClass } from '@/lib/utils'
import type { LiveStrategy } from '@hooks/useLivePositions'

export function PositionManager() {
  const strategies = useLivePositions()
  const unreadCount = useAlertStore((s) => s.unreadCount)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [checkedLegs, setCheckedLegs] = useState<Set<string>>(new Set())
  const [exitTarget, setExitTarget] = useState<LiveStrategy | null>(null)

  const totalMTM = strategies.reduce((s, st) => s + st.liveMTM, 0)
  const selectedStrategy = strategies.find((s) => s.id === selectedId) ?? null

  function handleCheck(legId: string, checked: boolean) {
    setCheckedLegs((prev) => {
      const next = new Set(prev)
      checked ? next.add(legId) : next.delete(legId)
      return next
    })
  }

  function handleSelect(id: string) {
    setSelectedId((prev) => (prev === id ? null : id))
  }

  if (strategies.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full py-24 text-center">
        <Layers size={40} className="text-text-muted mb-4 opacity-40" />
        <p className="text-text-secondary text-sm mb-2">No active positions</p>
        <p className="text-text-muted text-xs mb-6">Go to Strategy Builder to create one</p>
        <Link
          to="/builder"
          className="px-4 py-2 text-sm font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
        >
          Open Strategy Builder
        </Link>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Summary bar */}
      <div className="grid grid-cols-5 gap-3 p-4 border-b border-border-subtle bg-surface-1 shrink-0">
        <MetricCard
          label="Total MTM"
          value={fmtINRCompact(totalMTM)}
          valueClass={profitLossClass(totalMTM)}
          trend={totalMTM > 0 ? 'up' : totalMTM < 0 ? 'down' : null}
          compact
        />
        <MetricCard label="Strategies" value={strategies.length} compact />
        <MetricCard label="Active Legs" value={strategies.reduce((s, st) => s + st.legs.filter((l) => l.status === 'FILLED').length, 0)} compact />
        <MetricCard
          label="Alerts"
          value={unreadCount}
          valueClass={unreadCount > 0 ? 'text-loss' : 'text-text-muted'}
          compact
        />
        <MetricCard
          label="Peak MTM"
          value={strategies.length ? fmtINRCompact(Math.max(...strategies.map((s) => s.peakProfit ?? s.liveMTM))) : '—'}
          valueClass="text-profit"
          compact
        />
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto custom-scroll">
        <table className="trading-table">
          <thead>
            <tr>
              <th className="text-left w-8"></th>
              <th className="text-left">Instrument</th>
              <th>Qty</th>
              <th>Entry</th>
              <th>LTP</th>
              <th>Chg%</th>
              <th>MTM</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {strategies.map((strategy) => (
              <StrategyRow
                key={strategy.id}
                strategy={strategy}
                checkedLegs={checkedLegs}
                onCheck={handleCheck}
                onSelect={() => handleSelect(strategy.id)}
                isSelected={selectedId === strategy.id}
              />
            ))}
          </tbody>
        </table>
      </div>

      {/* Payoff detail panel */}
      {selectedStrategy && (
        <PayoffDetailPanel
          strategy={selectedStrategy}
          onClose={() => setSelectedId(null)}
          onExitStrategy={(s) => setExitTarget(s as LiveStrategy)}
        />
      )}

      {exitTarget && (
        <ExitConfirmModal
          open={true}
          onOpenChange={(open) => { if (!open) setExitTarget(null) }}
          strategy={exitTarget}
        />
      )}
    </div>
  )
}
