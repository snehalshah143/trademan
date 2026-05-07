import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  MoreVertical, Bell, Pause, Play, X as CloseIcon,
  Trash2, Edit2, TrendingUp, TrendingDown,
} from 'lucide-react'
import { toast } from 'react-hot-toast'
import { cn, fmtINRCompact } from '@/lib/utils'
import { monitorService } from '@/services/monitorService'
import type { MonitoredPosition } from '@/services/monitorService'
import { useLTPStore } from '@store/ltpStore'


interface MonitoredPositionCardProps {
  position: MonitoredPosition
  onManageAlerts: (position: MonitoredPosition) => void
  onEditPrices: (position: MonitoredPosition) => void
}

function statusBadge(status: string) {
  const cls =
    status === 'ACTIVE' ? 'bg-profit/10 text-profit border-profit/30' :
    status === 'PAUSED' ? 'bg-accent-amber/10 text-accent-amber border-accent-amber/30' :
    'bg-text-muted/10 text-text-muted border-border-default'
  return (
    <span className={cn('text-[10px] px-1.5 py-0.5 rounded border font-medium', cls)}>
      {status}
    </span>
  )
}

function pctColor(pct: number, side: 'BUY' | 'SELL'): string {
  if (pct === 0) return 'text-text-muted'
  // Profit = price fell for SELL, price rose for BUY
  const isProfit = side === 'SELL' ? pct < 0 : pct > 0
  return isProfit ? 'text-profit' : 'text-loss'
}

export function MonitoredPositionCard({
  position,
  onManageAlerts,
  onEditPrices,
}: MonitoredPositionCardProps) {
  const qc = useQueryClient()
  const [menuOpen, setMenuOpen] = useState(false)
  // Subscribe to live LTP from store
  const ltpMap = useLTPStore(s => s.ltpMap)

  // When WS sends MONITOR_UPDATE, update live MTM
  // (The useMarketWebSocket hook will need to dispatch these to a store or direct to components)
  // For now, compute live MTM from LTP store
  const computedLegs = position.legs.map(leg => {
    const ltp = ltpMap[leg.instrument]?.tick.ltp ?? leg.current_price
    const qty = leg.quantity * leg.lot_size
    const pnl = leg.side === 'SELL'
      ? (leg.entry_price - ltp) * qty
      : (ltp - leg.entry_price) * qty
    const pct = leg.entry_price > 0
      ? (ltp - leg.entry_price) / leg.entry_price * 100
      : 0
    return { ...leg, current_price: ltp, pnl, premium_change_pct: pct }
  })
  const totalMtm = computedLegs.reduce((s, l) => s + l.pnl, 0)
  const totalEntry = position.legs.reduce((s, l) => s + l.entry_price * l.quantity * l.lot_size, 0)
  const totalMtmPct = totalEntry > 0 ? totalMtm / totalEntry * 100 : 0

  const statusMut = useMutation({
    mutationFn: (s: string) => monitorService.updateStatus(position.monitor_id, s),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitored-positions'] }),
    onError: () => toast.error('Failed to update status'),
  })
  const deleteMut = useMutation({
    mutationFn: () => monitorService.delete(position.monitor_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['monitored-positions'] })
      toast.success('Position deleted')
    },
    onError: () => toast.error('Failed to delete'),
  })

  const isActive = position.status === 'ACTIVE'

  return (
    <div className={cn(
      'bg-surface-2 border border-border-subtle rounded-xl overflow-hidden transition-all',
      totalMtm < 0 ? 'border-l-4 border-l-loss/50' : totalMtm > 0 ? 'border-l-4 border-l-profit/50' : ''
    )}>
      {/* Header */}
      <div className="flex items-start justify-between px-4 py-3 border-b border-border-subtle">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-text-primary">{position.name}</span>
            {statusBadge(position.status)}
            <span className="text-xs text-text-muted">{position.strategy_type.replace('_', ' ')}</span>
          </div>
          {position.notes && (
            <p className="text-[11px] text-text-muted mt-0.5 truncate">{position.notes}</p>
          )}
        </div>
        <div className="relative ml-2 shrink-0">
          <button
            onClick={() => setMenuOpen(m => !m)}
            className="p-1 text-text-muted hover:text-text-primary transition-colors"
          >
            <MoreVertical size={15} />
          </button>
          {menuOpen && (
            <div
              className="absolute right-0 top-6 z-20 bg-surface-1 border border-border-default rounded-lg shadow-modal min-w-40 py-1"
              onBlur={() => setMenuOpen(false)}
            >
              {[
                { label: 'Edit name/notes', icon: Edit2, action: () => {} },
                { label: 'Edit entry prices', icon: Edit2, action: () => { onEditPrices(position); setMenuOpen(false) } },
                { label: isActive ? 'Pause monitoring' : 'Resume monitoring', icon: isActive ? Pause : Play,
                  action: () => { statusMut.mutate(isActive ? 'PAUSED' : 'ACTIVE'); setMenuOpen(false) } },
                { label: 'Close position', icon: CloseIcon,
                  action: () => { statusMut.mutate('CLOSED'); setMenuOpen(false) } },
                { label: 'Delete', icon: Trash2,
                  action: () => { if (confirm('Delete this position and all its alerts?')) { deleteMut.mutate(); setMenuOpen(false) } } },
              ].map(({ label, icon: Icon, action }) => (
                <button
                  key={label}
                  onClick={action}
                  className="w-full flex items-center gap-2 px-3 py-2 text-xs text-text-secondary hover:text-text-primary hover:bg-surface-2 transition-colors text-left"
                >
                  <Icon size={12} />
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* MTM summary */}
      <div className="px-4 py-3 border-b border-border-subtle">
        <div className="flex items-baseline gap-3">
          <span className={cn('text-xl font-bold tabular-nums', totalMtm < 0 ? 'text-loss' : totalMtm > 0 ? 'text-profit' : 'text-text-primary')}>
            {fmtINRCompact(totalMtm)}
          </span>
          <span className={cn('text-xs tabular-nums', totalMtmPct < 0 ? 'text-loss' : totalMtmPct > 0 ? 'text-profit' : 'text-text-muted')}>
            {totalMtmPct >= 0 ? '+' : ''}{totalMtmPct.toFixed(2)}%
          </span>
          {totalMtm !== 0 && (totalMtm < 0 ? <TrendingDown size={14} className="text-loss" /> : <TrendingUp size={14} className="text-profit" />)}
        </div>
      </div>

      {/* Legs table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border-subtle text-text-muted">
              <th className="text-left px-3 py-1.5 w-6">#</th>
              <th className="text-left px-2 py-1.5">Side</th>
              <th className="text-left px-2 py-1.5">Instrument</th>
              <th className="text-right px-2 py-1.5">Entry</th>
              <th className="text-right px-2 py-1.5">Now</th>
              <th className="text-right px-2 py-1.5">Chg%</th>
              <th className="text-right px-3 py-1.5">PnL</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-subtle">
            {computedLegs.map((leg, i) => (
              <tr key={leg.leg_id} className="hover:bg-surface-3/30">
                <td className="px-3 py-1.5 text-text-muted">{i + 1}</td>
                <td className="px-2 py-1.5">
                  <span className={cn('font-medium', leg.side === 'BUY' ? 'text-profit' : 'text-loss')}>
                    {leg.side}
                  </span>
                </td>
                <td className="px-2 py-1.5 text-text-primary font-mono truncate max-w-[140px]">
                  {leg.instrument}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums text-text-secondary">
                  {leg.entry_price.toFixed(2)}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums text-text-primary">
                  {leg.current_price.toFixed(2)}
                </td>
                <td className={cn('px-2 py-1.5 text-right tabular-nums', pctColor(leg.premium_change_pct, leg.side))}>
                  {leg.premium_change_pct >= 0 ? '+' : ''}{leg.premium_change_pct.toFixed(1)}%
                </td>
                <td className={cn('px-3 py-1.5 text-right tabular-nums font-medium', leg.pnl < 0 ? 'text-loss' : leg.pnl > 0 ? 'text-profit' : 'text-text-muted')}>
                  {fmtINRCompact(leg.pnl)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between px-4 py-3 border-t border-border-subtle">
        <div className="text-xs text-text-muted">
          <span>Alerts: {position.alert_count} configured</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onManageAlerts(position)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-accent-blue/40 text-accent-blue rounded-md hover:bg-accent-blue/10 transition-colors"
          >
            <Bell size={12} />
            Manage Alerts
          </button>
          {isActive ? (
            <button
              onClick={() => statusMut.mutate('PAUSED')}
              className="flex items-center gap-1 px-3 py-1.5 text-xs border border-border-default text-text-secondary rounded-md hover:bg-surface-3 transition-colors"
            >
              <Pause size={12} />
              Pause
            </button>
          ) : position.status === 'PAUSED' ? (
            <button
              onClick={() => statusMut.mutate('ACTIVE')}
              className="flex items-center gap-1 px-3 py-1.5 text-xs border border-profit/40 text-profit rounded-md hover:bg-profit/10 transition-colors"
            >
              <Play size={12} />
              Resume
            </button>
          ) : null}
          {position.status !== 'CLOSED' && (
            <button
              onClick={() => statusMut.mutate('CLOSED')}
              className="px-3 py-1.5 text-xs border border-loss/40 text-loss rounded-md hover:bg-loss/10 transition-colors"
            >
              Close
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
