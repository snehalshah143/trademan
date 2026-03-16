import { useState, useMemo, useEffect, useRef } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { ChevronDown, X, Search } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import axios from 'axios'
import { format, parseISO } from 'date-fns'

import { useLTPStore } from '@store/ltpStore'
import { useInstruments } from '@hooks/useInstruments'
import { useQuote, spotExchangeFor } from '@hooks/useQuote'
import { fmtPrice, fmtINRCompact, profitLossClass, cn, generateId } from '@/lib/utils'
import { PrebuiltStrategiesPanel } from './PrebuiltStrategiesPanel'
import { FuturesSelector } from './FuturesSelector'
import type { StrategyLeg, Exchange, InstrumentType, ProductType } from '@/types/domain'

const STRIKE_INTERVALS: Record<string, number> = {
  NIFTY: 50, BANKNIFTY: 100, FINNIFTY: 50, SENSEX: 100, MIDCPNIFTY: 50,
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeFutSymbol(underlying: string, expiry: string): string {
  try {
    const d = parseISO(expiry)
    return `${underlying}${format(d, 'dd')}${format(d, 'MMM').toUpperCase()}${format(d, 'yy')}FUT`
  } catch {
    return `${underlying}FUT`
  }
}

function fmtExpiryShort(expiry?: string): string {
  if (!expiry) return ''
  try { return format(parseISO(expiry), 'dd MMM') } catch { return expiry.slice(0, 6) }
}

function computeFallbackExpiries(count = 4): string[] {
  const expiries: string[] = []
  const today = new Date()
  let year = today.getFullYear()
  let month = today.getMonth()
  let attempts = 0
  while (expiries.length < count && attempts < count + 4) {
    const last = new Date(year, month + 1, 0)
    const offset = (last.getDay() - 4 + 7) % 7
    last.setDate(last.getDate() - offset)
    if (last > today) {
      const m = String(last.getMonth() + 1).padStart(2, '0')
      const d = String(last.getDate()).padStart(2, '0')
      expiries.push(`${last.getFullYear()}-${m}-${d}`)
    }
    month++; if (month > 11) { month = 0; year++ }
    attempts++
  }
  return expiries
}

function formatInstrumentLabel(leg: StrategyLeg): string {
  const { instrument } = leg
  if (instrument.instrumentType === 'FUT') {
    const und = instrument.symbol.match(/^([A-Z]+)/)?.[1] ?? instrument.symbol
    return `${und} FUT`
  }
  if (instrument.strike != null) {
    const und = instrument.symbol.match(/^([A-Z]+)/)?.[1] ?? ''
    return und ? `${und} ${instrument.strike} ${instrument.instrumentType}` : `${instrument.strike} ${instrument.instrumentType}`
  }
  return instrument.symbol
}

// ─── InstrumentHeader ────────────────────────────────────────────────────────

interface InstrumentHeaderProps {
  underlying: string
  lotSize: number
  legs: StrategyLeg[]
  selectedFutExpiry: string | null
  addFormSide: 'BUY' | 'SELL' | null
  onSelectFutExpiry: (expiry: string) => void
  onBSClick: (side: 'BUY' | 'SELL') => void
  onClearLegs: () => void
  onChangeUnderlying?: (symbol: string, lotSize: number) => void
}

export function InstrumentHeader({
  underlying, lotSize, legs,
  selectedFutExpiry, addFormSide,
  onSelectFutExpiry, onBSClick, onClearLegs, onChangeUnderlying,
}: InstrumentHeaderProps) {
  const ltpMap = useLTPStore((s) => s.ltpMap)
  const { instruments } = useInstruments()
  const [instOpen, setInstOpen] = useState(false)
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)

  // Quote API — day's change (ltp - prev_close), refreshes every 5 s during market hours
  const { data: quoteData } = useQuote(underlying, spotExchangeFor(underlying))

  // LTP: quote API is authoritative (fetched fresh every 5s during market hours).
  // Fall back to WS store only before the first quote response arrives.
  const wsLtp = ltpMap[underlying]?.tick.ltp ?? 0
  const spot  = quoteData?.ltp ?? wsLtp

  // Day's change from quote API; fall back to WS tick delta if quote not loaded
  const change    = quoteData?.change    ?? ltpMap[underlying]?.tick.change    ?? 0
  const changePct = quoteData?.changePct ?? ltpMap[underlying]?.tick.changePct ?? 0
  const isUp      = change >= 0

  // Same filtering as InstrumentSelector
  const filtered = useMemo(() => {
    if (!query) return instruments.slice(0, 40)
    const q = query.toLowerCase()
    return instruments.filter(
      (i) => i.symbol.toLowerCase().includes(q) || i.fullName.toLowerCase().includes(q)
    ).slice(0, 20)
  }, [instruments, query])

  useEffect(() => {
    if (instOpen) setTimeout(() => searchRef.current?.focus(), 50)
    else setQuery('')
  }, [instOpen])

  return (
    <div className="flex items-center justify-between px-3 py-2.5 border-b border-border-subtle bg-surface-1 shrink-0 gap-3">

      {/* Left: instrument + LTP */}
      <div className="flex flex-col gap-0.5 min-w-0">
        <Popover.Root open={instOpen} onOpenChange={onChangeUnderlying ? setInstOpen : undefined}>
          <Popover.Trigger asChild>
            <button
              disabled={!onChangeUnderlying}
              className="flex items-center gap-1 text-sm font-semibold text-text-primary hover:text-accent-blue transition-colors disabled:cursor-default text-left"
            >
              <span>{underlying}</span>
              <span className="text-xs font-normal text-text-muted">(Lot size: {lotSize})</span>
              {onChangeUnderlying && (
                <ChevronDown size={12} className={cn('text-text-muted shrink-0 transition-transform', instOpen && 'rotate-180')} />
              )}
            </button>
          </Popover.Trigger>
          {onChangeUnderlying && (
            <Popover.Portal>
              <Popover.Content
                className="w-72 bg-surface-1 border border-border-default rounded-lg shadow-modal z-50 overflow-hidden animate-fade-in"
                align="start"
                sideOffset={4}
              >
                {/* Search */}
                <div className="flex items-center gap-2 px-3 py-2 border-b border-border-subtle">
                  <Search size={12} className="text-text-muted shrink-0" />
                  <input
                    ref={searchRef}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search instrument…"
                    className="flex-1 bg-transparent text-xs text-text-primary placeholder-text-muted outline-none"
                  />
                  {query && (
                    <button onClick={() => setQuery('')} className="text-text-muted hover:text-text-primary text-xs">×</button>
                  )}
                </div>
                {/* List */}
                <div className="max-h-60 overflow-y-auto custom-scroll py-1">
                  {filtered.map((inst) => (
                    <button
                      key={inst.symbol}
                      onClick={() => { onChangeUnderlying(inst.symbol, inst.lotSize); setInstOpen(false) }}
                      className={cn(
                        'w-full flex items-center justify-between px-3 py-2 text-xs hover:bg-surface-2 transition-colors',
                        inst.symbol === underlying ? 'text-accent-blue font-semibold' : 'text-text-secondary'
                      )}
                    >
                      <div className="text-left">
                        <div className="font-bold">{inst.symbol}</div>
                        <div className="text-[10px] text-text-muted truncate max-w-[140px]">{inst.fullName}</div>
                      </div>
                      <span className="text-text-muted text-[10px] shrink-0 ml-2">Lot: {inst.lotSize}</span>
                    </button>
                  ))}
                  {filtered.length === 0 && (
                    <div className="px-3 py-4 text-center text-xs text-text-muted">No instruments match</div>
                  )}
                </div>
              </Popover.Content>
            </Popover.Portal>
          )}
        </Popover.Root>

        <div className="flex items-center gap-1.5">
          <span className="text-xs font-mono text-text-primary">{spot > 0 ? fmtPrice(spot) : '—'}</span>
          {spot > 0 && (
            <span className={cn('text-[11px] font-mono', isUp ? 'text-profit' : 'text-loss')}>
              {isUp ? '+' : ''}{fmtPrice(change, 2)} ({isUp ? '+' : ''}{changePct.toFixed(2)}%)
            </span>
          )}
        </div>
      </div>

      {/* Right: FUT selector + B/S + Clear */}
      <div className="flex items-center gap-2 shrink-0">
        <FuturesSelector
          underlying={underlying}
          legs={legs}
          selectedExpiry={selectedFutExpiry}
          onSelectExpiry={onSelectFutExpiry}
        />

        <button
          onClick={() => onBSClick('BUY')}
          title="Add BUY leg"
          className={cn(
            'w-7 h-7 flex items-center justify-center rounded text-[11px] font-bold transition-colors',
            addFormSide === 'BUY'
              ? 'bg-blue-600 text-white ring-1 ring-accent-blue'
              : 'bg-accent-blue hover:bg-blue-600 text-white'
          )}
        >B</button>

        <button
          onClick={() => onBSClick('SELL')}
          title="Add SELL leg"
          className={cn(
            'w-7 h-7 flex items-center justify-center rounded text-[11px] font-bold transition-colors',
            addFormSide === 'SELL'
              ? 'bg-red-600 text-white ring-1 ring-red-500'
              : 'bg-red-500 hover:bg-red-600 text-white'
          )}
        >S</button>

        <button
          onClick={onClearLegs}
          className="text-xs text-text-muted hover:text-loss transition-colors px-1"
        >
          Clear
        </button>
      </div>
    </div>
  )
}

// ─── AddLegForm ──────────────────────────────────────────────────────────────

interface AddLegFormProps {
  side: 'BUY' | 'SELL'
  underlying: string
  lotSize: number
  defaultExpiry: string | null
  onAdd: (leg: StrategyLeg) => void
  onCancel: () => void
}

function AddLegForm({ side, underlying, lotSize, defaultExpiry, onAdd, onCancel }: AddLegFormProps) {
  const ltpMap = useLTPStore((s) => s.ltpMap)
  const spot = ltpMap[underlying]?.tick.ltp ?? 22500
  const interval = STRIKE_INTERVALS[underlying] ?? 50
  const atm = Math.round(spot / interval) * interval

  // Fetch expiries from API (shares cache with FuturesSelector)
  const { data: apiExpiries } = useQuery({
    queryKey: ['futExpiries', underlying],
    queryFn: async () => {
      const res = await axios.get<{ expiries: string[] }>(
        `/api/instruments/expiries?symbol=${underlying}&exchange=NFO&type=FUT`
      )
      return res.data.expiries ?? []
    },
    staleTime: 60_000,
    retry: 0,
  })

  const expiries = useMemo(
    () => (apiExpiries && apiExpiries.length > 0 ? apiExpiries : computeFallbackExpiries()).slice(0, 6),
    [apiExpiries]
  )

  const [optType, setOptType]   = useState<'CE' | 'PE' | 'FUT'>('FUT')
  const [strike, setStrike]     = useState<number>(atm)
  const [expiry, setExpiry]     = useState<string>(defaultExpiry ?? '')
  const [lots, setLots]         = useState<number>(1)
  const [ltpInput, setLtpInput] = useState<string>('')
  const [product, setProduct]   = useState<ProductType>('NRML')

  // Set default expiry once expiries load
  useEffect(() => {
    if (!expiry && expiries.length > 0) setExpiry(expiries[0])
  }, [expiries]) // eslint-disable-line

  // Build option symbol for LTP lookup
  const symbol = useMemo(() => {
    if (optType === 'FUT') return expiry ? makeFutSymbol(underlying, expiry) : `${underlying}FUT`
    const exShort = expiry ? expiry.replace(/-/g, '').slice(2) : ''
    return `${underlying}${exShort}${strike}${optType}`
  }, [underlying, optType, expiry, strike])

  const storedLTP = ltpMap[symbol]?.tick.ltp ?? 0

  function handleAdd() {
    const entryPrice = ltpInput !== '' ? parseFloat(ltpInput) : storedLTP
    const newLeg: StrategyLeg = {
      id: generateId('leg'),
      legIndex: 0,
      instrument: {
        symbol,
        exchange: 'NFO' as Exchange,
        instrumentType: optType as InstrumentType,
        expiry: expiry || undefined,
        strike: optType !== 'FUT' ? strike : undefined,
        lotSize,
        tickSize: 0.05,
      },
      side,
      lots,
      quantity: lots * lotSize,
      productType: product,
      orderType: 'MARKET',
      entryPrice: entryPrice > 0 ? entryPrice : undefined,
      status: 'DRAFT',
      isHedge: side === 'SELL',
    }
    onAdd(newLeg)
  }

  const field = 'flex flex-col gap-0.5'
  const lbl   = 'text-[10px] text-text-muted'
  const inp   = 'bg-surface-3 border border-border-default rounded px-2 py-1.5 text-xs text-text-primary outline-none focus:border-accent-blue'

  return (
    <div className={cn(
      'px-3 py-2.5 border-b-2 border-border-subtle bg-surface-2 shrink-0',
      side === 'BUY' ? 'border-l-2 border-l-accent-blue' : 'border-l-2 border-l-red-500'
    )}>
      <div className="flex items-end gap-2 flex-wrap">

        {/* Strike — only for CE/PE */}
        {optType !== 'FUT' && (
          <div className={field}>
            <label className={lbl}>Strike</label>
            <input
              type="number"
              step={interval}
              value={strike}
              onChange={(e) => setStrike(Number(e.target.value))}
              className={cn(inp, 'w-20 text-right')}
            />
          </div>
        )}

        {/* Type */}
        <div className={field}>
          <label className={lbl}>Type</label>
          <select
            value={optType}
            onChange={(e) => setOptType(e.target.value as 'CE' | 'PE' | 'FUT')}
            className={cn(inp, 'w-16')}
          >
            <option value="FUT">FUT</option>
            <option value="CE">CE</option>
            <option value="PE">PE</option>
          </select>
        </div>

        {/* Expiry */}
        <div className={field}>
          <label className={lbl}>Expiry</label>
          <select
            value={expiry}
            onChange={(e) => setExpiry(e.target.value)}
            className={cn(inp, 'w-24')}
          >
            {expiries.map((exp) => (
              <option key={exp} value={exp}>{fmtExpiryShort(exp)}</option>
            ))}
          </select>
        </div>

        {/* Lots */}
        <div className={field}>
          <label className={lbl}>Lots</label>
          <input
            type="number"
            min={1}
            value={lots}
            onChange={(e) => setLots(Math.max(1, parseInt(e.target.value) || 1))}
            className={cn(inp, 'w-14 text-right')}
          />
        </div>

        {/* LTP / Entry */}
        <div className={field}>
          <label className={lbl}>
            LTP{storedLTP > 0 ? ` (${storedLTP.toFixed(2)})` : ''}
          </label>
          <input
            type="number"
            step="0.05"
            placeholder={storedLTP > 0 ? storedLTP.toFixed(2) : '0.00'}
            value={ltpInput}
            onChange={(e) => setLtpInput(e.target.value)}
            className={cn(inp, 'w-20 text-right')}
          />
        </div>

        {/* Product */}
        <div className={field}>
          <label className={lbl}>Product</label>
          <select
            value={product}
            onChange={(e) => setProduct(e.target.value as ProductType)}
            className={cn(inp, 'w-16')}
          >
            <option value="NRML">NRML</option>
            <option value="MIS">MIS</option>
          </select>
        </div>

        {/* Action buttons */}
        <div className="flex items-end gap-2 ml-auto pb-0.5">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-xs border border-border-default text-text-secondary hover:text-text-primary rounded transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleAdd}
            disabled={!expiry || lots < 1}
            className={cn(
              'px-3 py-1.5 text-xs font-semibold rounded text-white transition-colors disabled:opacity-50',
              side === 'BUY' ? 'bg-accent-blue hover:bg-blue-600' : 'bg-red-500 hover:bg-red-600'
            )}
          >
            Add {side}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── LegTableRow ─────────────────────────────────────────────────────────────

interface LegTableRowProps {
  leg: StrategyLeg
  index: number
  onRemove: (id: string) => void
  onEditLots: (id: string, lots: number) => void
  onEditEntryPrice: (id: string, price: number) => void
}

function LegTableRow({ leg, index, onRemove, onEditLots, onEditEntryPrice }: LegTableRowProps) {
  const ltpEntry = useLTPStore((s) => s.ltpMap[leg.instrument.symbol])
  const wsLtp    = ltpEntry?.tick.ltp

  // Fall back to quote API for symbols not in WS stream (options, stock legs, etc.)
  const { data: quoteData } = useQuote(
    leg.instrument.symbol,
    leg.instrument.exchange ?? 'NFO',
    wsLtp == null,  // only poll when WS has no data
  )

  const ltp       = wsLtp ?? quoteData?.ltp ?? leg.currentLTP ?? leg.entryPrice ?? 0
  const direction = ltpEntry?.direction ?? 'flat'
  const flashKey  = ltpEntry?.flashKey ?? 0

  const [editingEntry, setEditingEntry] = useState(false)
  const [entryInput, setEntryInput]     = useState('')
  const [editingLots, setEditingLots]   = useState(false)
  const [lotsInput, setLotsInput]       = useState('')

  const entryPrice = leg.entryPrice ?? 0
  const sideMult   = leg.side === 'BUY' ? 1 : -1
  const pnl        = sideMult * (ltp - entryPrice) * leg.quantity

  const instrumentLabel = formatInstrumentLabel(leg)

  function commitEntry() {
    const v = parseFloat(entryInput)
    if (!isNaN(v) && v > 0) onEditEntryPrice(leg.id, v)
    setEditingEntry(false)
  }
  function commitLots() {
    const v = parseInt(lotsInput)
    if (!isNaN(v) && v > 0) onEditLots(leg.id, v)
    setEditingLots(false)
  }

  return (
    <tr className="border-b border-border-subtle hover:bg-surface-2 transition-colors text-xs">
      {/* # */}
      <td className="px-2 py-2 text-center font-mono text-text-muted w-7">{index}</td>

      {/* Side */}
      <td className="px-2 py-2 text-center w-14">
        <span className={cn(
          'px-2 py-0.5 rounded text-[10px] font-bold',
          leg.side === 'BUY' ? 'bg-accent-blue/20 text-accent-blue' : 'bg-red-500/20 text-red-400'
        )}>
          {leg.side}
        </span>
      </td>

      {/* Instrument */}
      <td className="px-2 py-2 text-left font-mono text-text-secondary text-[11px]">
        {instrumentLabel}
      </td>

      {/* Expiry */}
      <td className="px-2 py-2 text-left text-text-muted whitespace-nowrap">
        {fmtExpiryShort(leg.instrument.expiry)}
      </td>

      {/* Lots — click to edit inline */}
      <td className="px-2 py-2 text-center font-mono w-14">
        {editingLots ? (
          <input
            autoFocus
            type="number"
            min={1}
            value={lotsInput}
            onChange={(e) => setLotsInput(e.target.value)}
            onBlur={commitLots}
            onKeyDown={(e) => { if (e.key === 'Enter') commitLots(); if (e.key === 'Escape') setEditingLots(false) }}
            className="w-12 text-center px-1 py-0.5 bg-surface-3 border border-accent-blue rounded text-text-primary focus:outline-none"
          />
        ) : (
          <button
            onClick={() => { setLotsInput(String(leg.lots)); setEditingLots(true) }}
            className="text-text-secondary hover:text-text-primary font-mono w-full text-center"
            title="Click to edit"
          >
            {leg.lots}
          </button>
        )}
      </td>

      {/* Entry price — click to edit inline */}
      <td className="px-2 py-2 text-right font-mono w-20">
        {editingEntry ? (
          <input
            autoFocus
            type="number"
            step="0.05"
            value={entryInput}
            onChange={(e) => setEntryInput(e.target.value)}
            onBlur={commitEntry}
            onKeyDown={(e) => { if (e.key === 'Enter') commitEntry(); if (e.key === 'Escape') setEditingEntry(false) }}
            className="w-16 text-right px-1 py-0.5 bg-surface-3 border border-accent-blue rounded text-text-primary focus:outline-none"
          />
        ) : (
          <button
            onClick={() => { setEntryInput(String(leg.entryPrice ?? '')); setEditingEntry(true) }}
            className="text-text-secondary hover:text-text-primary transition-colors w-full text-right"
            title="Click to edit"
          >
            {entryPrice > 0 ? fmtPrice(entryPrice) : <span className="text-text-muted italic">—</span>}
          </button>
        )}
      </td>

      {/* LTP — with flash */}
      <td className="px-2 py-2 text-right font-mono text-text-primary w-20">
        <span
          key={flashKey}
          className={cn(
            direction === 'up'   && 'ltp-flash-up',
            direction === 'down' && 'ltp-flash-down'
          )}
        >
          {fmtPrice(ltp)}
        </span>
      </td>

      {/* P&L */}
      <td className={cn('px-2 py-2 text-right font-mono font-medium w-20', profitLossClass(pnl))}>
        {pnl >= 0 ? '+' : ''}{fmtINRCompact(pnl)}
      </td>

      {/* Product */}
      <td className="px-2 py-2 text-center text-text-muted w-14">
        {leg.productType}
      </td>

      {/* Remove */}
      <td className="px-1 py-2 text-center w-6">
        <button
          onClick={() => onRemove(leg.id)}
          className="text-text-muted hover:text-loss transition-colors p-0.5 rounded"
          title="Remove leg"
        >
          <X size={12} />
        </button>
      </td>
    </tr>
  )
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface PositionsTabProps {
  underlying: string
  lotSize: number
  legs: StrategyLeg[]
  enabledLegs: Set<string>
  expiry: string
  addFormSide: 'BUY' | 'SELL' | null
  selectedFutExpiry: string | null
  onAddLeg: (leg: StrategyLeg) => void
  onSetAddFormSide: (side: 'BUY' | 'SELL' | null) => void
  onToggleLeg: (id: string, enabled: boolean) => void
  onEditLots: (id: string, lots: number) => void
  onToggleSide: (id: string) => void
  onRemoveLeg: (id: string) => void
  onEditEntryPrice: (id: string, price: number) => void
  onLoadPreset: (legs: StrategyLeg[]) => void
  onSwitchToOptionChain: () => void
  onSwitchToLegEditor: () => void
}

// ─── Main component ───────────────────────────────────────────────────────────

export function PositionsTab({
  underlying,
  lotSize,
  legs,
  enabledLegs,
  expiry,
  addFormSide,
  selectedFutExpiry,
  onAddLeg,
  onSetAddFormSide,
  onToggleLeg: _onToggleLeg,
  onEditLots,
  onToggleSide: _onToggleSide,
  onRemoveLeg,
  onEditEntryPrice,
  onLoadPreset,
  onSwitchToOptionChain,
  onSwitchToLegEditor,
}: PositionsTabProps) {
  const ltpMap = useLTPStore((s) => s.ltpMap)

  function handleAddFromForm(leg: StrategyLeg) {
    onAddLeg(leg)
    onSetAddFormSide(null)
  }

  // Total MTM across all legs
  const totalMTM = useMemo(
    () =>
      legs.reduce((sum, leg) => {
        const ltp = ltpMap[leg.instrument.symbol]?.tick.ltp ?? leg.currentLTP ?? leg.entryPrice ?? 0
        return sum + (leg.side === 'BUY' ? 1 : -1) * (ltp - (leg.entryPrice ?? 0)) * leg.quantity
      }, 0),
    [legs, ltpMap]
  )

  const totalLots = useMemo(() => legs.reduce((s, l) => s + l.lots, 0), [legs])

  // Suppress unused-var warnings for props kept for API compat
  void enabledLegs

  return (
    <div className="flex flex-col h-full">

      {/* ── Inline add-leg form ─────────────────────────────────────────── */}
      {addFormSide && (
        <AddLegForm
          side={addFormSide}
          underlying={underlying}
          lotSize={lotSize}
          defaultExpiry={selectedFutExpiry}
          onAdd={handleAddFromForm}
          onCancel={() => onSetAddFormSide(null)}
        />
      )}

      {/* ── Content ─────────────────────────────────────────────────────── */}
      {legs.length === 0 ? (

        /* Prebuilt strategies panel when no legs */
        <div className="flex-1 overflow-y-auto custom-scroll">
          <PrebuiltStrategiesPanel
            underlying={underlying}
            lotSize={lotSize}
            expiry={expiry}
            onLoadPreset={onLoadPreset}
            onBuildOptionChain={onSwitchToOptionChain}
            onBuildLegEditor={onSwitchToLegEditor}
          />
        </div>

      ) : (
        <>
          {/* Legs table */}
          <div className="flex-1 overflow-y-auto custom-scroll">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface-1 z-10 border-b border-border-subtle">
                <tr className="text-[10px] text-text-muted uppercase">
                  <th className="px-2 py-1.5 text-center w-7">#</th>
                  <th className="px-2 py-1.5 text-center w-14">Side</th>
                  <th className="px-2 py-1.5 text-left">Instrument</th>
                  <th className="px-2 py-1.5 text-left">Expiry</th>
                  <th className="px-2 py-1.5 text-center w-14">Lots</th>
                  <th className="px-2 py-1.5 text-right w-20">Entry</th>
                  <th className="px-2 py-1.5 text-right w-20">LTP</th>
                  <th className="px-2 py-1.5 text-right w-20">P&amp;L</th>
                  <th className="px-2 py-1.5 text-center w-14">Product</th>
                  <th className="px-1 py-1.5 w-6"></th>
                </tr>
              </thead>
              <tbody>
                {legs.map((leg, idx) => (
                  <LegTableRow
                    key={leg.id}
                    leg={leg}
                    index={idx + 1}
                    onRemove={onRemoveLeg}
                    onEditLots={onEditLots}
                    onEditEntryPrice={onEditEntryPrice}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {/* Strategy MTM footer bar */}
          <div className="shrink-0 px-3 py-2 border-t border-border-subtle bg-surface-1 flex items-center gap-4">
            <span className="text-xs text-text-muted">
              {legs.length} leg{legs.length !== 1 ? 's' : ''}
            </span>
            <span className="text-xs text-text-muted">
              {totalLots} lot{totalLots !== 1 ? 's' : ''}
            </span>
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-text-muted">MTM</span>
              <span className={cn('text-sm font-mono font-bold', profitLossClass(totalMTM))}>
                {totalMTM >= 0 ? '+' : ''}{fmtINRCompact(totalMTM)}
              </span>
            </div>
            <button className="ml-auto text-xs text-text-secondary border border-border-default rounded px-2.5 py-1 hover:border-accent-amber hover:text-accent-amber transition-colors">
              Set SL / Target
            </button>
          </div>
        </>
      )}
    </div>
  )
}
