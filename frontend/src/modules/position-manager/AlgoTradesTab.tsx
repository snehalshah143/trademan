import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Activity, RefreshCw } from 'lucide-react'
import { cn, fmtINRCompact, profitLossClass } from '@/lib/utils'
import { MetricCard } from '@/components/ui/MetricCard'
import { useLTPStore } from '@store/ltpStore'
import {
  algoTradesService,
  type AlgoRun,
  type AlgoRunDetail,
} from '@/services/algoTradesService'

// ── Date helpers ──────────────────────────────────────────────────────────────

function toISODate(d: Date): string {
  return d.toISOString().slice(0, 10)
}
function todayISO(): string { return toISODate(new Date()) }
function daysAgoISO(n: number): string {
  const d = new Date(); d.setDate(d.getDate() - n); return toISODate(d)
}

type StatusFilter = 'OPEN' | 'CLOSED' | 'BOTH'
type DatePreset   = 'TODAY' | 'WEEK' | 'MONTH' | 'CUSTOM'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTime(s: string | null | undefined) {
  if (!s) return '—'
  return new Date(s).toLocaleString('en-IN', {
    day: '2-digit', month: 'short',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function strategyLabel(run: { strategy_type: string; spread_type: string | null; direction: string | null }) {
  if (run.strategy_type === 'DIRECTIONAL_FUTURES') {
    return run.direction ?? 'FUTURES'
  }
  const map: Record<string, string> = {
    BULL_PUT_SPREAD:  'Bull Put',
    BEAR_CALL_SPREAD: 'Bear Call',
    IRON_FLY:         'Iron Fly',
    IRON_CONDOR:      'Iron Condor',
  }
  return run.spread_type ? (map[run.spread_type] ?? run.spread_type) : run.strategy_type
}

// ── Status + Mode badges ──────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={cn(
      'px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide',
      status === 'OPEN'   ? 'bg-profit/15 text-profit' :
      status === 'CLOSED' ? 'bg-surface-3 text-text-muted' :
      'bg-accent-amber/15 text-accent-amber'
    )}>
      {status}
    </span>
  )
}

// ── Leg row — matches LegRow style from StrategyRow ───────────────────────────

function AlgoLegRow({ leg, legNum, lotSize, snapshotLtp, legSl }: {
  leg:          AlgoRunDetail['legs'][0]
  legNum:       number
  lotSize:      number
  snapshotLtp?: number | null   // last_*_ltp from MTM snapshot (monitoring script)
  legSl?:       number | null   // per-leg stoploss (trailing SL price)
}) {
  // OPEN leg  → live WS price (if subscribed), else snapshot from monitoring script (~30s fresh)
  // CLOSED leg → no LTP (position is done; exit_price already shown in the Exit ₹ column)
  const wsLtp = useLTPStore(s =>
    leg.status === 'OPEN' ? (s.ltpMap[leg.symbol]?.tick.ltp ?? null) : null
  )
  const displayLtp =
    leg.status === 'CLOSED'
      ? null
      : (wsLtp ?? snapshotLtp ?? null)

  const isBuy = leg.action === 'BUY' || leg.direction === 'LONG'
  const sideLabel = leg.action ?? (leg.direction === 'LONG' ? 'BUY' : leg.direction === 'SHORT' ? 'SELL' : null)
  const lots      = lotSize > 0 ? Math.round(leg.quantity / lotSize) : leg.quantity
  const signedLots = isBuy ? lots : -lots
  const signedQty  = isBuy ? leg.quantity : -leg.quantity
  const pnl        = leg.status === 'CLOSED' ? leg.realized_pnl : null
  const rowBg      = pnl == null ? '' : pnl > 0 ? 'bg-green-500/4' : pnl < 0 ? 'bg-red-500/4' : ''

  return (
    <tr className={cn('border-b border-border-subtle hover:bg-surface-4 transition-colors', rowBg)}>
      {/* Leg label + BUY/SELL */}
      <td className="px-4 py-2.5">
        <div className="flex items-center gap-1">
          <span className={cn(
            'text-[10px] font-bold px-1.5 py-0.5 rounded',
            isBuy ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'
          )}>
            L{legNum}
          </span>
          {sideLabel && (
            <span className={cn(
              'text-[9px] font-bold px-1 py-0.5 rounded uppercase tracking-wide',
              isBuy ? 'text-profit' : 'text-loss'
            )}>
              {sideLabel}
            </span>
          )}
        </div>
      </td>

      {/* Instrument */}
      <td className="px-3 py-2.5 text-left">
        <span className="text-xs text-text-primary font-medium whitespace-nowrap">{leg.symbol}</span>
      </td>

      {/* Product */}
      <td className="px-3 py-2.5 text-center">
        <span className="text-[11px] font-semibold text-accent-amber">{leg.product ?? 'NRML'}</span>
      </td>

      {/* Lots */}
      <td className="px-3 py-2.5 text-center">
        <span className={cn(
          'inline-block px-2.5 py-0.5 rounded text-[11px] font-bold num min-w-[28px] text-center',
          isBuy ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'
        )}>
          {signedLots}
        </span>
      </td>

      {/* Qty */}
      <td className="px-3 py-2.5 text-center">
        <span className={cn(
          'inline-block px-2.5 py-0.5 rounded text-[11px] font-semibold num min-w-[44px] text-center',
          isBuy ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'
        )}>
          {signedQty}
        </span>
      </td>

      {/* Avg Price + entry time */}
      <td className="px-3 py-2.5 text-right" title={leg.entry_time ? `Entry: ${fmtTime(leg.entry_time)}` : undefined}>
        <span className="num text-xs text-text-secondary">
          {leg.entry_price != null ? leg.entry_price.toFixed(2) : '0'}
        </span>
        {leg.entry_time && (
          <div className="text-[9px] text-text-muted leading-tight">{fmtTime(leg.entry_time)}</div>
        )}
      </td>

      {/* Exit Price + exit time */}
      <td className="px-3 py-2.5 text-right" title={leg.exit_time ? `Exit: ${fmtTime(leg.exit_time)}` : undefined}>
        <span className="num text-xs text-text-secondary">
          {leg.exit_price != null ? leg.exit_price.toFixed(2) : '—'}
        </span>
        {leg.exit_time && (
          <div className="text-[9px] text-text-muted leading-tight">{fmtTime(leg.exit_time)}</div>
        )}
      </td>

      {/* LTP — live from WS store, fallback to snapshot */}
      <td className="px-3 py-2.5 text-right">
        {displayLtp != null ? (
          <span className="num text-xs font-semibold text-accent-blue">
            {displayLtp.toFixed(2)}
          </span>
        ) : (
          <span className="text-[10px] text-text-muted">—</span>
        )}
      </td>

      {/* Per-leg SL */}
      <td className="px-3 py-2.5 text-right">
        {legSl != null ? (
          <span className="num text-xs font-semibold text-loss">
            {legSl.toFixed(2)}
          </span>
        ) : (
          <span className="text-[10px] text-text-muted">—</span>
        )}
      </td>

      {/* P&L */}
      <td className="px-3 py-2.5 text-right">
        <span className={cn(
          'num text-xs font-semibold',
          pnl == null ? 'text-text-muted' :
          pnl >= 0    ? 'text-profit' : 'text-loss'
        )}>
          {pnl != null ? `${pnl >= 0 ? '+' : ''}${fmtINRCompact(pnl)}` : '—'}
        </span>
      </td>

      {/* Status */}
      <td className="px-3 py-2.5 text-center">
        <span className={cn(
          'px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase',
          leg.status === 'OPEN'   ? 'bg-profit/10 text-profit' :
          leg.status === 'CLOSED' ? 'bg-surface-3 text-text-muted' :
          'bg-accent-amber/10 text-accent-amber'
        )}>
          {leg.status}
        </span>
      </td>
      {/* Trailing empty cell to match 12-column table */}
      <td />
    </tr>
  )
}

// ── Single algo run row (expandable) ─────────────────────────────────────────

function AlgoRunRow({ run }: { run: AlgoRun }) {
  const [expanded, setExpanded] = useState(false)

  const { data: detail, isLoading } = useQuery({
    queryKey:        ['algo-run', run.id],
    queryFn:         () => algoTradesService.getRun(run.id),
    enabled:         expanded,
    staleTime:       30_000,
    // Refresh snapshot LTP every 30s while the row is expanded and run is OPEN
    refetchInterval: expanded && run.status === 'OPEN' ? 30_000 : false,
  })

  const pnl      = run.status === 'CLOSED' ? run.realized_pnl : run.latest_mtm
  const pnlLabel = run.status === 'CLOSED' ? 'Realized' : 'MTM'

  // After detail loads: decide where SL/Target belong
  const legCount   = detail?.legs.length ?? 0
  const currentSl  = detail?.current_sl ?? null
  const target     = detail?.max_profit  ?? null
  const maxLoss    = detail?.max_loss    ?? null

  // Single-leg run → current_sl belongs at leg level, NOT in header
  // Multi-leg run  → current_sl / max_loss is strategy-level, show in header
  const isMultiLeg        = legCount > 1
  const headerSl          = isMultiLeg ? currentSl : null
  const headerMaxLoss     = isMultiLeg ? maxLoss    : null
  const headerTarget      = target   // max_profit is always strategy-level

  // Per-leg snapshot LTP assignment:
  // futures strategy → single leg gets futures_ltp
  // credit spread    → primary=last_primary_ltp, hedge=last_hedge_ltp
  function snapshotLtpForLeg(leg: AlgoRunDetail['legs'][0]): number | null {
    if (!detail) return null
    if (leg.role === 'PRIMARY') return detail.last_primary_ltp ?? null
    if (leg.role === 'HEDGE')   return detail.last_hedge_ltp   ?? null
    return detail.last_futures_ltp ?? null
  }

  // Per-leg SL: only for single-leg runs (trailing SL = current_sl from snapshot)
  function slForLeg(_leg: AlgoRunDetail['legs'][0]): number | null {
    if (isMultiLeg) return null          // multi-leg: SL is at strategy level
    return currentSl                     // single-leg: trailing SL is per-leg
  }

  return (
    <>
      {/* Header row */}
      <tr
        className={cn(
          'cursor-pointer border-b border-border-default transition-colors',
          expanded ? 'bg-surface-3' : 'bg-surface-2 hover:bg-surface-3'
        )}
        onClick={() => setExpanded((v) => !v)}
      >
        {/* Expand + name — colSpan must equal (total columns − 2) so total = 12 */}
        <td className="px-4 py-3" colSpan={10}>
          <div className="flex items-center gap-2 flex-wrap">
            {expanded
              ? <ChevronDown size={14} className="text-text-muted shrink-0" />
              : <ChevronRight size={14} className="text-text-muted shrink-0" />
            }
            <span className="text-sm font-semibold text-text-primary">{run.strategy_name}</span>
            <span className="text-xs text-text-muted">#{run.strategy_id}</span>
            <StatusBadge status={run.status} />
            <span className="text-[10px] text-text-muted">
              {run.underlying} · {strategyLabel(run)} · {run.expiry}
            </span>
            {run.lots != null && (
              <span className="text-[10px] text-text-muted">
                · {run.lots} lot{run.lots !== 1 ? 's' : ''}
              </span>
            )}
            {run.exit_reason && (
              <span className="text-[10px] text-accent-amber">· {run.exit_reason}</span>
            )}
            {/* Combined SL/Target badges — strategy level only (multi-leg or fixed) */}
            {headerSl != null && (
              <span className="text-[10px] bg-loss/10 text-loss px-1.5 py-0.5 rounded font-semibold num">
                SL {headerSl.toFixed(2)}
              </span>
            )}
            {headerMaxLoss != null && headerSl == null && (
              <span className="text-[10px] bg-loss/10 text-loss px-1.5 py-0.5 rounded font-semibold num">
                Max Loss {fmtINRCompact(headerMaxLoss)}
              </span>
            )}
            {headerTarget != null && (
              <span className="text-[10px] bg-profit/10 text-profit px-1.5 py-0.5 rounded font-semibold num">
                Target {fmtINRCompact(headerTarget)}
              </span>
            )}
          </div>
        </td>

        {/* P&L + Entry time */}
        <td className="px-3 py-3 text-right" onClick={(e) => e.stopPropagation()}>
          <div className="flex flex-col items-end gap-0.5">
            {pnl != null && (
              <span className={cn('num font-bold text-sm', profitLossClass(pnl))}>
                {pnl >= 0 ? '+' : ''}{fmtINRCompact(pnl)}
              </span>
            )}
            <span className="text-[10px] text-text-muted">{pnlLabel}</span>
            <span className="text-[10px] text-text-secondary font-medium" title="Entry time">
              ▶ {fmtTime(run.entry_time)}
            </span>
            {run.exit_time && (
              <span className="text-[10px] text-text-muted" title="Exit time">
                ■ {fmtTime(run.exit_time)}
              </span>
            )}
          </div>
        </td>

        <td />
      </tr>

      {/* Column headers (once per run when expanded) */}
      {expanded && (
        <tr className="bg-surface-2 border-b border-border-subtle text-[10px] text-text-muted uppercase tracking-wider font-medium">
          <th className="px-4 py-1.5 text-left w-24">Leg</th>
          <th className="px-3 py-1.5 text-left">Instrument</th>
          <th className="px-3 py-1.5 text-center w-20">Product</th>
          <th className="px-3 py-1.5 text-center w-16">Lots</th>
          <th className="px-3 py-1.5 text-center w-24">Qty</th>
          <th className="px-3 py-1.5 text-right w-20">Avg Price</th>
          <th className="px-3 py-1.5 text-right w-20">Exit ₹</th>
          <th className="px-3 py-1.5 text-right w-20">LTP</th>
          <th className="px-3 py-1.5 text-right w-20">SL</th>
          <th className="px-3 py-1.5 text-right w-28">P &amp; L</th>
          <th className="px-3 py-1.5 text-center w-16">Status</th>
          <th />
        </tr>
      )}

      {/* Legs */}
      {expanded && isLoading && (
        <tr>
          <td colSpan={12} className="px-4 py-3 text-xs text-text-muted">Loading legs…</td>
        </tr>
      )}
      {expanded && detail?.legs.map((leg, i) => (
        <AlgoLegRow
          key={leg.id}
          leg={leg}
          legNum={i + 1}
          lotSize={detail.lot_size ?? 1}
          snapshotLtp={snapshotLtpForLeg(leg)}
          legSl={slForLeg(leg)}
        />
      ))}

      {/* Spacer */}
      <tr className="h-2 bg-transparent" />
    </>
  )
}

// ── Section ───────────────────────────────────────────────────────────────────

function Section({ label, icon, runs, emptyText }: {
  label: string
  icon: React.ReactNode
  runs: AlgoRun[]
  emptyText: string
}) {
  return (
    <>
      <div className="px-4 pt-3 pb-1 flex items-center gap-2">
        {icon}
        <span className="text-xs font-semibold text-text-muted uppercase tracking-wider">{label}</span>
        <span className="text-[10px] text-text-muted bg-surface-3 px-1.5 py-0.5 rounded">
          {runs.length}
        </span>
      </div>

      {runs.length === 0 ? (
        <div className="px-4 py-3 text-xs text-text-muted">{emptyText}</div>
      ) : (
        <table className="w-full border-collapse">
          <tbody>
            {runs.map((run) => (
              <AlgoRunRow key={run.id} run={run} />
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}

// ── Filter bar ────────────────────────────────────────────────────────────────

function PillBtn({ active, onClick, children }: {
  active: boolean; onClick: () => void; children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'px-2.5 py-1 text-[11px] font-medium rounded transition-colors',
        active
          ? 'bg-accent-blue text-white'
          : 'bg-surface-3 text-text-muted hover:text-text-primary hover:bg-surface-4'
      )}
    >
      {children}
    </button>
  )
}

// ── Main tab component ────────────────────────────────────────────────────────

export function AlgoTradesTab() {
  // ── Filter state ──────────────────────────────────────────────────────────
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('BOTH')
  const [datePreset,   setDatePreset]   = useState<DatePreset>('TODAY')
  const [customFrom,   setCustomFrom]   = useState(daysAgoISO(7))
  const [customTo,     setCustomTo]     = useState(todayISO())

  // ── Build query params from filter state ─────────────────────────────────
  const queryParams = useMemo(() => {
    const p: Parameters<typeof algoTradesService.listRuns>[0] = {}

    if (statusFilter === 'OPEN') {
      // All open trades — no date filter needed
      p.status = 'OPEN'
      return p
    }
    if (statusFilter === 'CLOSED') {
      p.status = 'CLOSED'
    }
    // Date range
    if (datePreset === 'TODAY') {
      p.date_from = todayISO()
      p.date_to   = todayISO()
      if (statusFilter === 'BOTH') p.include_open_all_time = true
    } else if (datePreset === 'WEEK') {
      p.date_from = daysAgoISO(7)
      p.date_to   = todayISO()
    } else if (datePreset === 'MONTH') {
      p.date_from = daysAgoISO(30)
      p.date_to   = todayISO()
    } else {
      // CUSTOM
      if (customFrom) p.date_from = customFrom
      if (customTo)   p.date_to   = customTo
    }
    return p
  }, [statusFilter, datePreset, customFrom, customTo])

  const { data: runs = [], isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['algo-runs', queryParams],
    queryFn:  () => algoTradesService.listRuns(queryParams),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const PAPER_MODES = new Set(['MONITOR_ONLY', 'EXIT_EXEC_MONITOR', 'PAPER'])
  const LIVE_MODES  = new Set(['ENTRY_EXEC_MONITOR', 'FULL_EXEC', 'LIVE'])
  const paperRuns = runs.filter((r) => PAPER_MODES.has(r.trade_mode))
  const liveRuns  = runs.filter((r) => LIVE_MODES.has(r.trade_mode))

  const openRuns      = runs.filter((r) => r.status === 'OPEN')
  const totalRealized = runs
    .filter((r) => r.status === 'CLOSED' && r.realized_pnl != null)
    .reduce((s, r) => s + (r.realized_pnl ?? 0), 0)
  const liveMtm = liveRuns
    .filter((r) => r.status === 'OPEN' && r.latest_mtm != null)
    .reduce((s, r) => s + (r.latest_mtm ?? 0), 0)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-text-muted text-xs">
        Loading algo trades…
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-loss text-xs">
        <Activity size={20} className="opacity-50" />
        <span>Could not load algo trades.</span>
        <span className="text-text-muted text-[10px]">Is the backend running?</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* ── Filter toolbar ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-border-subtle bg-surface-1 shrink-0">

        {/* Status filter */}
        <div className="flex items-center gap-1">
          <span className="text-[10px] text-text-muted uppercase tracking-wide mr-1">Status</span>
          <PillBtn active={statusFilter === 'OPEN'}   onClick={() => setStatusFilter('OPEN')}>Open</PillBtn>
          <PillBtn active={statusFilter === 'CLOSED'} onClick={() => setStatusFilter('CLOSED')}>Closed</PillBtn>
          <PillBtn active={statusFilter === 'BOTH'}   onClick={() => setStatusFilter('BOTH')}>Both</PillBtn>
        </div>

        <div className="w-px h-4 bg-border-subtle" />

        {/* Date preset — hidden when status=OPEN (date irrelevant for all-open view) */}
        {statusFilter !== 'OPEN' && (
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-text-muted uppercase tracking-wide mr-1">Date</span>
            <PillBtn active={datePreset === 'TODAY'} onClick={() => setDatePreset('TODAY')}>Today</PillBtn>
            <PillBtn active={datePreset === 'WEEK'}  onClick={() => setDatePreset('WEEK')}>7 days</PillBtn>
            <PillBtn active={datePreset === 'MONTH'} onClick={() => setDatePreset('MONTH')}>30 days</PillBtn>
            <PillBtn active={datePreset === 'CUSTOM'} onClick={() => setDatePreset('CUSTOM')}>Custom</PillBtn>
          </div>
        )}

        {/* Custom date pickers */}
        {statusFilter !== 'OPEN' && datePreset === 'CUSTOM' && (
          <div className="flex items-center gap-1.5">
            <input
              type="date"
              value={customFrom}
              onChange={e => setCustomFrom(e.target.value)}
              className="px-2 py-0.5 text-[11px] bg-surface-2 border border-border-default rounded text-text-primary focus:outline-none focus:border-accent-blue"
            />
            <span className="text-[10px] text-text-muted">—</span>
            <input
              type="date"
              value={customTo}
              onChange={e => setCustomTo(e.target.value)}
              className="px-2 py-0.5 text-[11px] bg-surface-2 border border-border-default rounded text-text-primary focus:outline-none focus:border-accent-blue"
            />
          </div>
        )}

        {/* Today default hint */}
        {statusFilter === 'BOTH' && datePreset === 'TODAY' && (
          <span className="text-[10px] text-text-muted">
            Today's trades + all prior open
          </span>
        )}

        <div className="ml-auto flex items-center gap-2">
          <span className="text-[10px] text-text-muted">Auto-refresh 30s</span>
          <button
            onClick={() => refetch()}
            className="p-1.5 text-text-muted hover:text-text-primary transition-colors"
            title="Refresh now"
          >
            <RefreshCw size={12} className={isFetching ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* ── Summary bar ────────────────────────────────────────────────── */}
      <div className="grid grid-cols-4 gap-3 p-4 border-b border-border-subtle bg-surface-1 shrink-0">
        <MetricCard label="Runs"         value={runs.length}       compact />
        <MetricCard label="Open"         value={openRuns.length}   compact />
        <MetricCard
          label="Live MTM"
          value={fmtINRCompact(liveMtm)}
          valueClass={profitLossClass(liveMtm)}
          trend={liveMtm > 0 ? 'up' : liveMtm < 0 ? 'down' : null}
          compact
        />
        <MetricCard
          label="Realized"
          value={fmtINRCompact(totalRealized)}
          valueClass={profitLossClass(totalRealized)}
          trend={totalRealized > 0 ? 'up' : totalRealized < 0 ? 'down' : null}
          compact
        />
      </div>

      {/* ── Sections ───────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto custom-scroll">
        {runs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-center">
            <Activity size={36} className="text-text-muted mb-3 opacity-40" />
            <p className="text-text-secondary text-sm mb-1">No trades match this filter</p>
            <p className="text-text-muted text-xs">Try a different date range or status.</p>
          </div>
        ) : (
          <>
            <Section
              label="Live Execution"
              icon={<span className="text-[10px] font-bold text-loss">⚡</span>}
              runs={liveRuns}
              emptyText="No live executions in this range."
            />
            <Section
              label="Paper Trade"
              icon={<span className="text-[10px] font-bold text-accent-blue">◎</span>}
              runs={paperRuns}
              emptyText="No paper trades in this range."
            />
          </>
        )}
      </div>
    </div>
  )
}
