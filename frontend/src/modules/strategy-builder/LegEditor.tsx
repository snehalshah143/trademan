import { useState, useEffect, useRef } from 'react'
import { Plus } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import axios from 'axios'
import { generateId, cn } from '@/lib/utils'
import { getStaticInstrument } from '@/data/instruments'
import type { StrategyLeg, InstrumentType, OrderSide, ProductType, OrderType, Exchange } from '@/types/domain'

interface LegEditorProps {
  onAddLeg: (leg: StrategyLeg) => void
  defaultUnderlying?: string
  defaultExpiry?: string
}

async function fetchExpiries(symbol: string, itype: string): Promise<string[]> {
  const params = itype === 'FUT'
    ? `symbol=${symbol}&exchange=NFO&type=FUT`
    : `symbol=${symbol}&exchange=NFO`
  const res = await axios.get<{ expiries: string[] }>(`/api/instruments/expiries?${params}`)
  return res.data.expiries ?? []
}

interface ChainResp { rows: { strike: number }[]; atm_strike: number | null }

async function fetchStrikes(symbol: string, expiry: string): Promise<{ strikes: number[]; atm: number | null }> {
  const res = await axios.get<ChainResp>(
    `/api/instruments/optionchain?symbol=${symbol}&exchange=NFO&expiry=${expiry}&strike_count=50`
  )
  const strikes = res.data.rows.map((r) => r.strike).sort((a, b) => a - b)
  return { strikes, atm: res.data.atm_strike }
}

function formatExpiryLabel(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' })
  } catch { return iso }
}

function buildSymbol(underlying: string, expiry: string, instrumentType: InstrumentType, strike: number): string {
  try {
    const d   = new Date(expiry)
    const dd  = String(d.getDate()).padStart(2, '0')
    const mon = d.toLocaleDateString('en-US', { month: 'short' }).toUpperCase()
    const yy  = String(d.getFullYear()).slice(2)
    return instrumentType === 'FUT'
      ? `${underlying}${dd}${mon}${yy}FUT`
      : `${underlying}${dd}${mon}${yy}${strike}${instrumentType}`
  } catch {
    return instrumentType === 'FUT' ? `${underlying}FUT` : `${underlying}${strike}${instrumentType}`
  }
}

// ─── Strike combobox ─────────────────────────────────────────────────────────

interface StrikeComboProps {
  strikes: number[]
  value: number
  loading: boolean
  disabled: boolean
  onChange: (s: number) => void
}

function StrikeCombo({ strikes, value, loading, disabled, onChange }: StrikeComboProps) {
  const [query, setQuery]   = useState('')
  const [open, setOpen]     = useState(false)
  const containerRef        = useRef<HTMLDivElement>(null)

  const filtered = query.trim()
    ? strikes.filter((s) => String(s).includes(query.trim()))
    : strikes

  // Close on outside click
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (!containerRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Sync display when value changes externally (e.g. ATM auto-select)
  useEffect(() => {
    if (value) setQuery('')
  }, [value])

  const displayValue = query || (value ? String(value) : '')

  function handleSelect(s: number) {
    onChange(s)
    setQuery('')
    setOpen(false)
  }

  function handleInput(e: React.ChangeEvent<HTMLInputElement>) {
    setQuery(e.target.value)
    setOpen(true)
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && filtered.length > 0) handleSelect(filtered[0])
    if (e.key === 'Escape') setOpen(false)
  }

  const inputClass = 'w-full px-2.5 py-1.5 text-sm bg-surface-3 border border-border-default rounded-md text-text-primary focus:outline-none focus:border-accent-blue'

  return (
    <div ref={containerRef} className="relative">
      <input
        type="text"
        inputMode="numeric"
        value={displayValue}
        placeholder={loading ? 'Loading…' : disabled ? 'Select expiry' : 'Search strike…'}
        disabled={disabled || loading}
        onChange={handleInput}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        className={inputClass}
        autoComplete="off"
      />
      {open && filtered.length > 0 && (
        <ul className="absolute z-50 mt-1 w-full max-h-48 overflow-y-auto bg-surface-1 border border-border-default rounded-md shadow-modal text-sm">
          {filtered.map((s) => (
            <li
              key={s}
              onMouseDown={() => handleSelect(s)}
              className={cn(
                'px-3 py-1.5 cursor-pointer hover:bg-surface-2',
                s === value && 'bg-[rgba(59,130,246,0.1)] text-accent-blue font-medium'
              )}
            >
              {s}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export function LegEditor({ onAddLeg, defaultUnderlying = 'NIFTY', defaultExpiry }: LegEditorProps) {
  const underlying = defaultUnderlying

  const [instrumentType, setInstrumentType] = useState<InstrumentType>('CE')
  const [side, setSide]               = useState<OrderSide>('SELL')
  const [expiry, setExpiry]           = useState(defaultExpiry ?? '')
  const [strike, setStrike]           = useState<number>(0)
  const [lots, setLots]               = useState(1)
  const [productType, setProductType] = useState<ProductType>('MIS')
  const [orderType, setOrderType]     = useState<OrderType>('MARKET')

  const inst    = getStaticInstrument(underlying)
  const lotSize = inst?.lotSize ?? 1

  // Expiries
  const { data: expiries = [] } = useQuery({
    queryKey: ['legEditorExpiries', underlying, instrumentType],
    queryFn:  () => fetchExpiries(underlying, instrumentType),
    staleTime: 60_000,
    retry: 0,
  })

  useEffect(() => {
    if (expiries.length > 0 && !expiries.includes(expiry)) {
      setExpiry(expiries[0])
    }
  }, [expiries])

  useEffect(() => { setExpiry(''); setStrike(0) }, [underlying])

  // Strikes
  const { data: strikeData, isFetching: loadingStrikes } = useQuery({
    queryKey: ['legEditorStrikes', underlying, expiry],
    queryFn:  () => fetchStrikes(underlying, expiry),
    staleTime: 30_000,
    retry: 0,
    enabled: !!expiry && instrumentType !== 'FUT',
  })

  const strikes = strikeData?.strikes ?? []

  useEffect(() => {
    if (strikes.length === 0) return
    const atm = strikeData?.atm
    setStrike(atm && strikes.includes(atm) ? atm : strikes[Math.floor(strikes.length / 2)])
  }, [strikes])

  useEffect(() => { setStrike(0) }, [expiry])

  // Add
  const canAdd = instrumentType === 'FUT' ? !!expiry : (!!expiry && strike > 0)

  function handleAdd() {
    if (!canAdd) return
    const symbol = buildSymbol(underlying, expiry, instrumentType, strike)
    const leg: StrategyLeg = {
      id: generateId('leg'),
      legIndex: 0,
      instrument: {
        symbol,
        exchange: (inst?.exchange ?? 'NFO') as Exchange,
        instrumentType,
        expiry: expiry || undefined,
        strike: instrumentType !== 'FUT' ? strike : undefined,
        lotSize,
        tickSize: 0.05,
      },
      side, lots, quantity: lots * lotSize,
      productType, orderType,
      status: 'DRAFT',
      isHedge: side === 'SELL',
    }
    onAddLeg(leg)
  }

  const selectClass = 'w-full px-2.5 py-1.5 text-sm bg-surface-3 border border-border-default rounded-md text-text-primary focus:outline-none focus:border-accent-blue'
  const labelClass  = 'text-xs text-text-muted mb-1 block'

  return (
    <div className="bg-surface-2 border border-border-subtle rounded-md p-4">
      <div className="grid grid-cols-4 gap-3 mb-3">
        <div>
          <label className={labelClass}>Type</label>
          <select value={instrumentType} onChange={(e) => setInstrumentType(e.target.value as InstrumentType)} className={selectClass}>
            <option value="CE">CE</option>
            <option value="PE">PE</option>
            <option value="FUT">FUT</option>
          </select>
        </div>
        <div>
          <label className={labelClass}>Side</label>
          <select value={side} onChange={(e) => setSide(e.target.value as OrderSide)} className={selectClass}>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
        </div>
        <div>
          <label className={labelClass}>Lots</label>
          <input type="number" min={1} value={lots} onChange={(e) => setLots(Number(e.target.value))} className={selectClass} />
        </div>
        <div>
          <label className={labelClass}>Product</label>
          <select value={productType} onChange={(e) => setProductType(e.target.value as ProductType)} className={selectClass}>
            <option value="MIS">MIS</option>
            <option value="NRML">NRML</option>
            <option value="CNC">CNC</option>
          </select>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-3 mb-3">
        <div>
          <label className={labelClass}>Expiry</label>
          <select
            value={expiry}
            onChange={(e) => { setExpiry(e.target.value); setStrike(0) }}
            className={selectClass}
            disabled={expiries.length === 0}
          >
            {expiries.length === 0
              ? <option value="">Loading…</option>
              : expiries.map((e) => <option key={e} value={e}>{formatExpiryLabel(e)}</option>)
            }
          </select>
        </div>

        {instrumentType !== 'FUT' && (
          <div>
            <label className={labelClass}>Strike</label>
            <StrikeCombo
              strikes={strikes}
              value={strike}
              loading={loadingStrikes}
              disabled={!expiry}
              onChange={setStrike}
            />
          </div>
        )}

        <div>
          <label className={labelClass}>Order type</label>
          <select value={orderType} onChange={(e) => setOrderType(e.target.value as OrderType)} className={selectClass}>
            <option value="MARKET">MARKET</option>
            <option value="LIMIT">LIMIT</option>
          </select>
        </div>

        <div className="flex items-end pb-0.5">
          <span className="text-xs text-text-muted">
            <span className="text-text-secondary font-medium">{underlying}</span>
            <span className="ml-1">· Lot {lotSize}</span>
          </span>
        </div>
      </div>

      <button
        onClick={handleAdd}
        disabled={!canAdd}
        className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-accent-blue hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-md transition-colors"
      >
        <Plus size={14} />
        Add Leg
      </button>
    </div>
  )
}
