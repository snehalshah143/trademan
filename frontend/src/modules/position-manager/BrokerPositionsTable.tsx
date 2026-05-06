import { useState } from 'react'
import { WifiOff, Plus, Trash2, RefreshCw, AlertCircle, FolderPlus } from 'lucide-react'
import { useBrokerPositions, useFunds } from '@hooks/useBrokerPositions'
import { useManualPositionStore } from '@store/manualPositionStore'
import { AddManualPositionModal } from './AddManualPositionModal'
import { CreateStrategyModal } from './CreateStrategyModal'
import { MetricCard } from '@/components/ui/MetricCard'
import { parseSymbol } from '@/lib/symbolParser'
import { fmtINRCompact, profitLossClass, cn } from '@/lib/utils'
import type { BrokerPosition } from '@/services/positionService'

function PnlCell({ value }: { value: number }) {
  return (
    <td className={cn('px-3 py-2 text-right num', profitLossClass(value))}>
      {fmtINRCompact(value)}
    </td>
  )
}

function QtyPill({ qty }: { qty: number }) {
  return (
    <td className="px-3 py-2 text-right">
      <span className={cn(
        'inline-block px-2 py-0.5 rounded text-[11px] font-semibold num',
        qty > 0 ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'
      )}>
        {qty > 0 ? `+${qty}` : qty}
      </span>
    </td>
  )
}

interface RowProps {
  p:        BrokerPosition
  selected: boolean
  onToggle: () => void
}

function PositionRow({ p, selected, onToggle }: RowProps) {
  const parsed = parseSymbol(p.symbol, p.exchange)
  const avg    = p.qty > 0 ? p.buy_avg : p.sell_avg

  return (
    <tr
      className={cn(
        'border-b border-border-subtle cursor-pointer transition-colors',
        selected ? 'bg-accent-blue/8 hover:bg-accent-blue/12' : 'hover:bg-surface-2'
      )}
      onClick={onToggle}
    >
      <td className="px-3 py-2.5 w-8">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          onClick={(e) => e.stopPropagation()}
          className="accent-accent-blue"
        />
      </td>
      <td className="px-3 py-2.5 text-xs text-text-primary font-medium">{parsed.displayName}</td>
      <td className="px-3 py-2.5 text-xs">
        <span className="text-accent-amber font-semibold">{p.product}</span>
      </td>
      <QtyPill qty={p.qty} />
      <td className="px-3 py-2.5 text-right num text-text-secondary text-xs">{avg.toFixed(2)}</td>
      <td className="px-3 py-2.5 text-right num text-text-primary text-xs">{p.ltp.toFixed(2)}</td>
      <PnlCell value={p.pnl} />
      <td className="px-3 py-2.5 text-xs">
        {p.strategy_id
          ? <span className="text-accent-blue">#{p.strategy_id.slice(0, 6)}</span>
          : <span className="text-text-muted">—</span>
        }
      </td>
    </tr>
  )
}

export function BrokerPositionsTable() {
  const [addOpen,       setAddOpen]       = useState(false)
  const [createOpen,    setCreateOpen]    = useState(false)
  const [selectedSyms,  setSelectedSyms]  = useState<Set<string>>(new Set())

  const { data: rawPositions, isLoading, isError, error, refetch, isFetching } = useBrokerPositions()
  const positions = rawPositions
    ? [...rawPositions].sort((a, b) => a.symbol.localeCompare(b.symbol))
    : rawPositions
  const { data: funds } = useFunds()
  const manualPositions = useManualPositionStore((s) => s.positions)
  const removeManual    = useManualPositionStore((s) => s.remove)

  // Connected if we have data OR are fetching (never flicker due to WS drops)
  const brokerConnected = !isError || rawPositions !== undefined
  const totalBrokerPnL  = (positions ?? []).reduce((s, p) => s + p.pnl, 0)
  const selectedPositions = (positions ?? []).filter((p) => selectedSyms.has(p.symbol))

  function toggleRow(symbol: string) {
    setSelectedSyms((prev) => {
      const next = new Set(prev)
      next.has(symbol) ? next.delete(symbol) : next.add(symbol)
      return next
    })
  }

  function toggleAll() {
    if (!positions) return
    if (selectedSyms.size === positions.length) {
      setSelectedSyms(new Set())
    } else {
      setSelectedSyms(new Set(positions.map((p) => p.symbol)))
    }
  }

  const allSelected = !!positions && positions.length > 0 && selectedSyms.size === positions.length

  return (
    <div className="flex flex-col h-full">

      {/* Connection banner */}
      {!brokerConnected && (
        <div className="flex items-center gap-2 px-4 py-2 bg-loss/10 border-b border-loss/30 text-loss text-xs shrink-0">
          <WifiOff size={13} />
          <span>OpenAlgo is not connected. Broker positions unavailable.</span>
        </div>
      )}

      {/* Funds bar */}
      {funds && (
        <div className="grid grid-cols-4 gap-3 px-4 py-3 border-b border-border-subtle bg-surface-1 shrink-0">
          <MetricCard label="Available"     value={fmtINRCompact(funds.available)} valueClass="text-profit" compact />
          <MetricCard label="Used Margin"   value={fmtINRCompact(funds.used)} compact />
          <MetricCard label="Total Balance" value={fmtINRCompact(funds.total)} compact />
          <MetricCard label="Broker P&L"    value={fmtINRCompact(totalBrokerPnL)} valueClass={profitLossClass(totalBrokerPnL)} compact />
        </div>
      )}

      <div className="flex-1 overflow-y-auto custom-scroll">

        {/* ── OpenAlgo Positions header ─────────────────────── */}
        <div className="px-4 pt-4 pb-2 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-text-secondary uppercase tracking-wider">OpenAlgo Positions</span>
            <span className={cn(
              'text-[10px] px-1.5 py-0.5 rounded-full font-medium',
              brokerConnected ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'
            )}>
              {brokerConnected ? 'Live' : 'Disconnected'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {selectedSyms.size > 0 && (
              <button
                onClick={() => setCreateOpen(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
              >
                <FolderPlus size={12} />
                Group as Strategy ({selectedSyms.size})
              </button>
            )}
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="p-1 text-text-muted hover:text-text-primary transition-colors disabled:opacity-40"
              title="Refresh"
            >
              <RefreshCw size={13} className={isFetching ? 'animate-spin' : ''} />
            </button>
          </div>
        </div>

        {/* selection hint */}
        {positions && positions.length > 0 && selectedSyms.size === 0 && (
          <p className="px-4 pb-2 text-[11px] text-text-muted">
            Select positions to group them into a strategy
          </p>
        )}

        {!brokerConnected ? (
          <div className="flex items-center gap-2 mx-4 my-3 px-3 py-3 rounded-lg bg-surface-2 text-text-muted text-xs">
            <AlertCircle size={13} />
            Configure OpenAlgo in Settings to see live broker positions.
          </div>
        ) : isLoading ? (
          <div className="px-4 py-4 text-xs text-text-muted">Loading positions…</div>
        ) : error ? (
          <div className="flex items-center gap-2 mx-4 my-3 px-3 py-3 rounded-lg bg-loss/10 text-loss text-xs">
            <AlertCircle size={13} />
            Failed to fetch broker positions. Check OpenAlgo connection.
          </div>
        ) : !positions || positions.length === 0 ? (
          <div className="px-4 py-4 text-xs text-text-muted">No open positions from broker.</div>
        ) : (
          <table className="trading-table mb-2">
            <thead>
              <tr>
                <th className="w-8 px-3">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleAll}
                    className="accent-accent-blue"
                  />
                </th>
                <th className="text-left">Trade Instrument</th>
                <th className="text-left">Product</th>
                <th className="text-right">Quantity</th>
                <th className="text-right">Avg Price</th>
                <th className="text-right">LTP</th>
                <th className="text-right">P&L</th>
                <th className="text-left">Strategy</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <PositionRow
                  key={p.symbol}
                  p={p}
                  selected={selectedSyms.has(p.symbol)}
                  onToggle={() => toggleRow(p.symbol)}
                />
              ))}
            </tbody>
          </table>
        )}

        {/* ── Manual Positions ───────────────────────────────── */}
        <div className="px-4 pt-4 pb-1 flex items-center justify-between border-t border-border-subtle mt-2">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Manual Positions</span>
            {manualPositions.length > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full font-medium bg-accent-amber/15 text-accent-amber">
                {manualPositions.length}
              </span>
            )}
          </div>
          <button
            onClick={() => setAddOpen(true)}
            className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
          >
            <Plus size={12} /> Add Position
          </button>
        </div>

        {manualPositions.length === 0 ? (
          <div className="px-4 py-4 text-xs text-text-muted">
            No manual positions. Click "Add Position" to track one.
          </div>
        ) : (
          <table className="trading-table mb-4">
            <thead>
              <tr>
                <th className="text-left">Symbol</th>
                <th className="text-left">Exch</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Buy Avg</th>
                <th className="text-right">Sell Avg</th>
                <th className="text-left">Product</th>
                <th className="text-left">Added</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {manualPositions.map((p) => (
                <tr key={p.id} className="hover:bg-surface-2 border-b border-border-subtle">
                  <td className="px-3 py-2 font-mono text-xs text-text-primary">{p.symbol}</td>
                  <td className="px-3 py-2 text-xs text-text-muted">{p.exchange}</td>
                  <td className="px-3 py-2 text-right">
                    <span className={cn('inline-block px-2 py-0.5 rounded text-[11px] font-semibold num', p.qty > 0 ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss')}>
                      {p.qty > 0 ? `+${p.qty}` : p.qty}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right num text-text-secondary text-xs">{p.buy_avg.toFixed(2)}</td>
                  <td className="px-3 py-2 text-right num text-text-secondary text-xs">{p.sell_avg.toFixed(2)}</td>
                  <td className="px-3 py-2 text-xs text-text-muted">{p.product}</td>
                  <td className="px-3 py-2 text-xs text-text-muted">
                    {new Date(p.addedAt).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button onClick={() => removeManual(p.id)} className="p-1 text-text-muted hover:text-loss transition-colors">
                      <Trash2 size={12} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <AddManualPositionModal open={addOpen} onOpenChange={setAddOpen} />
      <CreateStrategyModal
        open={createOpen}
        onOpenChange={setCreateOpen}
        positions={selectedPositions}
        onCreated={() => setSelectedSyms(new Set())}
      />
    </div>
  )
}
