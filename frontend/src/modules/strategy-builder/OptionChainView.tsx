import { useState, useRef } from 'react'
import { useOptionChain } from '@hooks/useOptionChain'
import { useLTPStore } from '@store/ltpStore'
import { ToggleSwitch } from '@/components/ui/ToggleSwitch'
import { generateId, cn } from '@/lib/utils'
import type { StrategyLeg, Exchange, InstrumentType, OrderSide } from '@/types/domain'
import { format, parseISO, differenceInCalendarDays } from 'date-fns'

const LOTS_OPTIONS   = [1, 2, 3, 4, 5, 10, 15, 20, 25, 50]
const STRIKE_COUNTS  = [5, 10, 15, 20] as const

interface OptionChainViewProps {
  underlying:       string
  lotSize:          number
  legs:             StrategyLeg[]
  onLegsChange:     (legs: StrategyLeg[]) => void
  controlledExpiry?: string                        // two-way sync with shared header
  onExpiryChange?:  (expiry: string) => void
}

function daysTo(expiry: string): number {
  try { return Math.max(0, differenceInCalendarDays(parseISO(expiry), new Date())) }
  catch { return 0 }
}

function formatExpiryTab(expiry: string): string {
  try { return format(parseISO(expiry), 'dd MMM') }
  catch { return expiry }
}

function fmtOI(v: number): string {
  if (!v) return '—'
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000)     return `${(v / 1_000).toFixed(0)}K`
  return String(v)
}

function fmtChange(v: number): string {
  if (!v) return ''
  return (v > 0 ? '+' : '') + fmtOI(v)
}

// ─── B/S button pair ─────────────────────────────────────────────────────────

interface BSButtonsProps {
  activeSide: 'BUY' | 'SELL' | null
  onBuy:  () => void
  onSell: () => void
  visible: boolean
}

function BSButtons({ activeSide, onBuy, onSell, visible }: BSButtonsProps) {
  return (
    <div className={cn('flex items-center gap-0.5 transition-opacity', !visible && 'opacity-0 pointer-events-none')}>
      <button
        onClick={(e) => { e.stopPropagation(); onBuy() }}
        className={cn(
          'px-1.5 py-0.5 rounded text-[10px] font-bold transition-colors',
          activeSide === 'BUY'
            ? 'bg-accent-blue text-white'
            : 'bg-[rgba(59,130,246,0.15)] text-accent-blue hover:bg-accent-blue hover:text-white'
        )}
      >B</button>
      <button
        onClick={(e) => { e.stopPropagation(); onSell() }}
        className={cn(
          'px-1.5 py-0.5 rounded text-[10px] font-bold transition-colors',
          activeSide === 'SELL'
            ? 'bg-red-500 text-white'
            : 'bg-[rgba(239,68,68,0.15)] text-red-400 hover:bg-red-500 hover:text-white'
        )}
      >S</button>
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export function OptionChainView({
  underlying, lotSize, legs, onLegsChange,
  controlledExpiry, onExpiryChange,
}: OptionChainViewProps) {
  const ltpMap = useLTPStore((s) => s.ltpMap)

  const [selectedExpiry, setSelectedExpiry] = useState<string | undefined>(undefined)

  // When parent-controlled expiry changes, sync to local state
  // (allows parent to drive the selected tab, e.g. FuturesSelector changes it)
  const activeControlled = controlledExpiry ?? undefined

  function handleExpirySelect(exp: string) {
    setSelectedExpiry(exp)
    onExpiryChange?.(exp)
  }
  const [strikeCount,     setStrikeCount]     = useState<number>(10)
  const [showPositions,   setShowPositions]   = useState(false)
  const [hoveredStrike,   setHoveredStrike]   = useState<number | null>(null)

  // Track previous LTP values for flash detection
  const prevLTPRef = useRef<Record<string, number>>({})

  const { chain, expiries, expiryInfos, expiriesSource, loading } = useOptionChain({
    symbol:      underlying,
    expiry:      selectedExpiry,
    strikeCount,
    polling:     true,
  })

  // Controlled expiry takes precedence; fall back to local selection or first available
  const activeExpiry = activeControlled ?? selectedExpiry ?? expiries[0] ?? ''

  // ─── Helpers ──────────────────────────────────────────────────────────────

  function getLeg(strike: number, type: 'CE' | 'PE'): StrategyLeg | undefined {
    return legs.find(
      (l) => l.instrument.strike === strike && l.instrument.instrumentType === type
    )
  }

  function makeSymbol(strike: number, type: 'CE' | 'PE'): string {
    const exShort = activeExpiry ? activeExpiry.replace(/-/g, '').slice(2) : ''
    return `${underlying}${exShort}${strike}${type}`
  }

  function buildNewLeg(strike: number, type: 'CE' | 'PE', side: OrderSide, ltp: number): StrategyLeg {
    const symbol = makeSymbol(strike, type)
    return {
      id:        generateId('leg'),
      legIndex:  legs.length,
      instrument: {
        symbol,
        exchange:       'NFO' as Exchange,
        instrumentType: type as InstrumentType,
        expiry:         activeExpiry || undefined,
        strike,
        lotSize,
        tickSize: 0.05,
      },
      side,
      lots:        1,
      quantity:    lotSize,
      productType: 'NRML',
      orderType:   'MARKET',
      entryPrice:  ltp > 0 ? ltp : undefined,
      status:      'DRAFT',
      isHedge:     side === 'SELL',
    }
  }

  function handleBS(strike: number, type: 'CE' | 'PE', side: OrderSide, ltp: number) {
    const existing = getLeg(strike, type)
    if (!existing) {
      onLegsChange([...legs, buildNewLeg(strike, type, side, ltp)])
    } else if (existing.side === side) {
      onLegsChange(legs.filter((l) => l.id !== existing.id))
    } else {
      onLegsChange(legs.map((l) =>
        l.id === existing.id ? { ...l, side, isHedge: side === 'SELL' } : l
      ))
    }
  }

  function handleLotsChange(strike: number, type: 'CE' | 'PE', lotsStr: string) {
    const lots = parseInt(lotsStr)
    if (isNaN(lots) || lots <= 0) return
    const existing = getLeg(strike, type)
    if (existing) {
      onLegsChange(legs.map((l) =>
        l.id === existing.id ? { ...l, lots, quantity: lots * lotSize } : l
      ))
    }
  }

  // ─── Flash helpers ────────────────────────────────────────────────────────

  function getFlash(key: string, currentVal: number): 'up' | 'down' | null {
    const prev = prevLTPRef.current[key]
    if (prev === undefined) { prevLTPRef.current[key] = currentVal; return null }
    if (currentVal > prev) { prevLTPRef.current[key] = currentVal; return 'up' }
    if (currentVal < prev) { prevLTPRef.current[key] = currentVal; return 'down' }
    return null
  }

  // ─── Error / empty states ─────────────────────────────────────────────────

  const errorCode = chain?.error_code
  const errorMsg  = chain?.error

  if (errorCode === 'master_contract') {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-6">
        <div className="text-2xl">📋</div>
        <p className="text-sm font-medium text-text-primary">Master Contract Not Downloaded</p>
        <p className="text-xs text-text-muted max-w-xs">
          OpenAlgo doesn't have NFO symbol data yet. Open OpenAlgo at{' '}
          <span className="font-mono text-accent-blue">127.0.0.1:5000</span>,
          go to Settings → Broker, and click <strong>Download Master Contract</strong>.
        </p>
        <p className="text-[10px] text-text-muted">Then reload the option chain here.</p>
      </div>
    )
  }

  if (errorCode === 'unreachable') {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-center px-6">
        <p className="text-sm font-medium text-loss">Broker Disconnected</p>
        <p className="text-xs text-text-muted">Check Settings — OpenAlgo must be running at the configured host.</p>
      </div>
    )
  }

  if (loading && !chain) {
    return (
      <div className="flex items-center justify-center h-48 text-text-muted text-xs">
        Loading option chain…
      </div>
    )
  }

  if (expiries.length === 0 && !loading) {
    return (
      <div className="flex flex-col items-center justify-center h-48 text-text-muted text-xs gap-2">
        <p>No expiry data available</p>
        <p className="text-[10px]">Run Sync in Settings → Instrument Data</p>
      </div>
    )
  }

  const rows      = chain?.rows ?? []
  const atmStrike = chain?.atm_strike
  const synFut    = chain?.synthetic_fut

  return (
    <div className="flex flex-col h-full">

      {/* ── Top bar: expiry tabs + strike count + positions toggle ── */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border-subtle bg-surface-1 shrink-0 gap-2">

        {/* Expiry tabs */}
        <div className="flex items-center gap-1 overflow-x-auto custom-scroll pb-0.5 flex-1 min-w-0">
          {expiries.slice(0, 8).map((exp) => {
            const info    = expiryInfos.find((e) => e.expiry === exp)
            const days    = info?.days_to_expiry ?? daysTo(exp)
            const label   = info?.display ? info.display : formatExpiryTab(exp)
            const isActive = activeExpiry === exp
            return (
              <button
                key={exp}
                onClick={() => handleExpirySelect(exp)}
                className={cn(
                  'shrink-0 px-2.5 py-1 rounded-md text-xs font-medium border transition-colors whitespace-nowrap',
                  isActive
                    ? 'bg-accent-blue border-accent-blue text-white'
                    : 'border-border-subtle text-text-muted hover:border-border-default hover:text-text-secondary'
                )}
              >
                {label} <span className="opacity-70">({days}d)</span>
              </button>
            )
          })}
          {expiriesSource && expiriesSource !== 'cached' && (
            <span className={cn(
              'shrink-0 ml-1 px-1.5 py-0.5 rounded text-[9px] font-medium uppercase',
              expiriesSource === 'live'     && 'bg-profit/10 text-profit',
              expiriesSource === 'computed' && 'bg-text-muted/10 text-text-muted',
            )}>
              {expiriesSource}
            </span>
          )}
        </div>

        {/* Strike count + positions toggle */}
        <div className="flex items-center gap-3 shrink-0">
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-text-muted">Strikes</span>
            <select
              value={strikeCount}
              onChange={(e) => setStrikeCount(Number(e.target.value))}
              className="text-xs bg-surface-3 border border-border-default rounded px-1.5 py-0.5 text-text-secondary outline-none focus:border-accent-blue"
            >
              {STRIKE_COUNTS.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-text-muted">Positions</span>
            <ToggleSwitch checked={showPositions} onCheckedChange={setShowPositions} size="sm" />
          </div>
        </div>
      </div>

      {/* ── Column headers ── */}
      <div className="shrink-0 border-b border-border-subtle bg-surface-2">
        <table className="w-full text-[10px] text-text-muted uppercase">
          <colgroup>
            <col style={{ width: '7%' }}  /> {/* Call OI */}
            <col style={{ width: '5%' }}  /> {/* Call Vol */}
            <col style={{ width: '5%' }}  /> {/* Call IV */}
            <col style={{ width: '5%' }}  /> {/* Call Δ */}
            <col style={{ width: '8%' }}  /> {/* Call LTP */}
            <col style={{ width: '7%' }}  /> {/* Call B/S */}
            <col style={{ width: '5%' }}  /> {/* Call Lots */}
            <col style={{ width: '8%' }}  /> {/* Strike */}
            <col style={{ width: '5%' }}  /> {/* Put Lots */}
            <col style={{ width: '7%' }}  /> {/* Put B/S */}
            <col style={{ width: '8%' }}  /> {/* Put LTP */}
            <col style={{ width: '5%' }}  /> {/* Put Δ */}
            <col style={{ width: '5%' }}  /> {/* Put IV */}
            <col style={{ width: '5%' }}  /> {/* Put Vol */}
            <col style={{ width: '7%' }}  /> {/* Put OI */}
          </colgroup>
          <thead>
            <tr>
              <th colSpan={7} className="text-center py-1 bg-[rgba(34,197,94,0.06)] text-profit/80 border-r border-border-subtle">
                CALLS
              </th>
              <th className="text-center py-1 bg-surface-3 font-bold text-text-secondary"></th>
              <th colSpan={7} className="text-center py-1 bg-[rgba(251,191,36,0.06)] text-accent-amber/80 border-l border-border-subtle">
                PUTS
              </th>
            </tr>
            <tr>
              <th className="px-1 py-1 text-right  bg-[rgba(34,197,94,0.04)]">OI</th>
              <th className="px-1 py-1 text-right  bg-[rgba(34,197,94,0.04)]">Vol</th>
              <th className="px-1 py-1 text-right  bg-[rgba(34,197,94,0.04)]">IV</th>
              <th className="px-1 py-1 text-right  bg-[rgba(34,197,94,0.04)]">Δ</th>
              <th className="px-1 py-1 text-right  bg-[rgba(34,197,94,0.04)]">LTP</th>
              <th className="px-1 py-1 text-center bg-[rgba(34,197,94,0.04)]">B/S</th>
              <th className="px-1 py-1 text-right  bg-[rgba(34,197,94,0.04)]">Lots</th>
              <th className="px-1 py-1 text-center bg-surface-2 font-bold text-text-secondary">Strike</th>
              <th className="px-1 py-1 text-left   bg-[rgba(251,191,36,0.04)]">Lots</th>
              <th className="px-1 py-1 text-center bg-[rgba(251,191,36,0.04)]">B/S</th>
              <th className="px-1 py-1 text-left   bg-[rgba(251,191,36,0.04)]">LTP</th>
              <th className="px-1 py-1 text-left   bg-[rgba(251,191,36,0.04)]">Δ</th>
              <th className="px-1 py-1 text-left   bg-[rgba(251,191,36,0.04)]">IV</th>
              <th className="px-1 py-1 text-left   bg-[rgba(251,191,36,0.04)]">Vol</th>
              <th className="px-1 py-1 text-left   bg-[rgba(251,191,36,0.04)]">OI</th>
            </tr>
          </thead>
        </table>
      </div>

      {/* ── Chain rows ── */}
      <div className="flex-1 overflow-y-auto custom-scroll">
        {rows.length === 0 && !loading && (
          <div className="text-center py-8 text-text-muted text-xs">
            {errorMsg
              ? errorMsg
              : expiries.length > 0
                ? 'No strikes available for this expiry'
                : 'Select an expiry to load the option chain'
            }
          </div>
        )}

        {loading && rows.length === 0 && (
          <div className="text-center py-8 text-text-muted text-xs animate-pulse">
            Fetching strikes…
          </div>
        )}

        <table className="w-full text-xs">
          <colgroup>
            <col style={{ width: '7%' }}  />
            <col style={{ width: '5%' }}  />
            <col style={{ width: '5%' }}  />
            <col style={{ width: '5%' }}  />
            <col style={{ width: '8%' }}  />
            <col style={{ width: '7%' }}  />
            <col style={{ width: '5%' }}  />
            <col style={{ width: '8%' }}  />
            <col style={{ width: '5%' }}  />
            <col style={{ width: '7%' }}  />
            <col style={{ width: '8%' }}  />
            <col style={{ width: '5%' }}  />
            <col style={{ width: '5%' }}  />
            <col style={{ width: '5%' }}  />
            <col style={{ width: '7%' }}  />
          </colgroup>
          <tbody>
            {rows.map((row) => {
              const isATM     = row.strike === atmStrike
              const isITMCall = row.strike < (atmStrike ?? 0)
              const isHovered = hoveredStrike === row.strike

              const callSym = makeSymbol(row.strike, 'CE')
              const putSym  = makeSymbol(row.strike, 'PE')

              // Use live WS LTP if available, else chain data
              const callLTP = ltpMap[callSym]?.tick.ltp ?? row.callLTP
              const putLTP  = ltpMap[putSym]?.tick.ltp  ?? row.putLTP

              // Flash from WS or chain polling
              const callDir = ltpMap[callSym]?.direction ?? getFlash(`c${row.strike}`, callLTP) ?? 'flat'
              const putDir  = ltpMap[putSym]?.direction  ?? getFlash(`p${row.strike}`, putLTP)  ?? 'flat'
              const callFlashKey = ltpMap[callSym]?.flashKey ?? 0
              const putFlashKey  = ltpMap[putSym]?.flashKey  ?? 0

              const callLeg = getLeg(row.strike, 'CE')
              const putLeg  = getLeg(row.strike, 'PE')
              const callActiveSide = callLeg?.side ?? null
              const putActiveSide  = putLeg?.side  ?? null
              const showCallCtrl = isHovered || !!callLeg
              const showPutCtrl  = isHovered || !!putLeg

              return (
                <tr
                  key={row.strike}
                  onMouseEnter={() => setHoveredStrike(row.strike)}
                  onMouseLeave={() => setHoveredStrike(null)}
                  className={cn(
                    'border-b border-border-subtle transition-colors',
                    isATM      && 'bg-[rgba(59,130,246,0.10)]',
                    !isATM && isITMCall && 'bg-[rgba(34,197,94,0.03)]',
                    isHovered && !isATM && 'bg-surface-2',
                    showPositions && callLeg && 'border-l-2 border-l-accent-blue',
                    showPositions && putLeg  && 'border-r-2 border-r-red-500',
                  )}
                >
                  {/* ── CALL side ── */}
                  <td className="px-1 py-1.5 text-right font-mono text-[10px] text-text-muted bg-[rgba(34,197,94,0.03)]">
                    <div>{fmtOI(row.callOI)}</div>
                    {row.callOIChange !== 0 && (
                      <div className={cn('text-[9px]', row.callOIChange > 0 ? 'text-profit' : 'text-loss')}>
                        {fmtChange(row.callOIChange)}
                      </div>
                    )}
                  </td>
                  <td className="px-1 py-1.5 text-right font-mono text-[10px] text-text-muted bg-[rgba(34,197,94,0.03)]">
                    {fmtOI(row.callVolume)}
                  </td>
                  <td className="px-1 py-1.5 text-right text-[10px] text-text-muted bg-[rgba(34,197,94,0.03)]">
                    {row.callIV ? `${row.callIV.toFixed(1)}` : '—'}
                  </td>
                  <td className="px-1 py-1.5 text-right text-[10px] text-text-muted bg-[rgba(34,197,94,0.03)]">
                    {row.callDelta ? row.callDelta.toFixed(2) : '—'}
                  </td>
                  <td className={cn(
                    'px-1 py-1.5 text-right font-mono font-medium bg-[rgba(34,197,94,0.03)]',
                    isATM ? 'text-accent-blue' : 'text-text-primary',
                    callLeg?.side === 'BUY'  && 'border-l-2 border-l-accent-blue',
                    callLeg?.side === 'SELL' && 'border-l-2 border-l-red-500',
                  )}>
                    <span
                      key={callFlashKey}
                      className={cn(
                        callDir === 'up'   && 'ltp-flash-up',
                        callDir === 'down' && 'ltp-flash-down',
                      )}
                    >
                      {callLTP > 0 ? callLTP.toFixed(2) : '—'}
                    </span>
                  </td>
                  <td className="px-1 py-1.5 bg-[rgba(34,197,94,0.03)]">
                    <div className="flex justify-center">
                      <BSButtons
                        activeSide={callActiveSide}
                        onBuy={()  => handleBS(row.strike, 'CE', 'BUY',  callLTP)}
                        onSell={() => handleBS(row.strike, 'CE', 'SELL', callLTP)}
                        visible={showCallCtrl}
                      />
                    </div>
                  </td>
                  <td className="px-1 py-1.5 text-right bg-[rgba(34,197,94,0.03)]">
                    {callLeg ? (
                      <select
                        value={callLeg.lots}
                        onChange={(e) => handleLotsChange(row.strike, 'CE', e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                        className="w-10 text-xs text-right bg-surface-3 border border-border-default rounded px-1 py-0.5 text-text-primary outline-none"
                      >
                        {LOTS_OPTIONS.map((n) => <option key={n} value={n}>{n}</option>)}
                      </select>
                    ) : <span className="text-text-muted text-[10px]">—</span>}
                  </td>

                  {/* ── Strike ── */}
                  <td className={cn(
                    'px-1 py-1.5 text-center font-bold font-mono bg-surface-2 relative',
                    isATM ? 'text-accent-blue text-[13px]' : 'text-text-primary',
                  )}>
                    {isATM && (
                      <span className="absolute left-0 top-0 bottom-0 w-0.5 bg-accent-blue" />
                    )}
                    {row.strike}
                    {isATM && (
                      <span className="block text-[8px] font-normal text-accent-blue/70 leading-none">ATM</span>
                    )}
                  </td>

                  {/* ── PUT side ── */}
                  <td className="px-1 py-1.5 text-left bg-[rgba(251,191,36,0.04)]">
                    {putLeg ? (
                      <select
                        value={putLeg.lots}
                        onChange={(e) => handleLotsChange(row.strike, 'PE', e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                        className="w-10 text-xs bg-surface-3 border border-border-default rounded px-1 py-0.5 text-text-primary outline-none"
                      >
                        {LOTS_OPTIONS.map((n) => <option key={n} value={n}>{n}</option>)}
                      </select>
                    ) : <span className="text-text-muted text-[10px]">—</span>}
                  </td>
                  <td className="px-1 py-1.5 bg-[rgba(251,191,36,0.04)]">
                    <div className="flex justify-center">
                      <BSButtons
                        activeSide={putActiveSide}
                        onBuy={()  => handleBS(row.strike, 'PE', 'BUY',  putLTP)}
                        onSell={() => handleBS(row.strike, 'PE', 'SELL', putLTP)}
                        visible={showPutCtrl}
                      />
                    </div>
                  </td>
                  <td className={cn(
                    'px-1 py-1.5 text-left font-mono font-medium bg-[rgba(251,191,36,0.04)]',
                    isATM ? 'text-accent-blue' : 'text-text-primary',
                    putLeg?.side === 'BUY'  && 'border-r-2 border-r-accent-blue',
                    putLeg?.side === 'SELL' && 'border-r-2 border-r-red-500',
                  )}>
                    <span
                      key={putFlashKey}
                      className={cn(
                        putDir === 'up'   && 'ltp-flash-up',
                        putDir === 'down' && 'ltp-flash-down',
                      )}
                    >
                      {putLTP > 0 ? putLTP.toFixed(2) : '—'}
                    </span>
                  </td>
                  <td className="px-1 py-1.5 text-left text-[10px] text-text-muted bg-[rgba(251,191,36,0.04)]">
                    {row.putDelta ? row.putDelta.toFixed(2) : '—'}
                  </td>
                  <td className="px-1 py-1.5 text-left text-[10px] text-text-muted bg-[rgba(251,191,36,0.04)]">
                    {row.putIV ? `${row.putIV.toFixed(1)}` : '—'}
                  </td>
                  <td className="px-1 py-1.5 text-left font-mono text-[10px] text-text-muted bg-[rgba(251,191,36,0.04)]">
                    {fmtOI(row.putVolume)}
                  </td>
                  <td className="px-1 py-1.5 text-left font-mono text-[10px] text-text-muted bg-[rgba(251,191,36,0.04)]">
                    <div>{fmtOI(row.putOI)}</div>
                    {row.putOIChange !== 0 && (
                      <div className={cn('text-[9px]', row.putOIChange > 0 ? 'text-profit' : 'text-loss')}>
                        {fmtChange(row.putOIChange)}
                      </div>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>

        {/* Synthetic FUT info bar */}
        {synFut && rows.length > 0 && (
          <div className="px-3 py-1.5 border-t border-border-subtle bg-surface-1 text-[10px] text-text-muted flex items-center gap-3">
            <span>Synthetic FUT <span className="font-mono text-accent-blue">{synFut.toFixed(2)}</span></span>
            {chain?.spot && (
              <span>Spot <span className="font-mono text-text-secondary">{chain.spot.toFixed(2)}</span></span>
            )}
            <span className="ml-auto text-[9px] opacity-60">Polling every 3s</span>
          </div>
        )}
      </div>
    </div>
  )
}
