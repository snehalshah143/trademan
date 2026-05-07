import { useState, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { X, Plus, Trash2, AlertCircle, CheckCircle2 } from 'lucide-react'
import axios from 'axios'
import { toast } from 'react-hot-toast'
import { cn } from '@/lib/utils'
import { monitorService } from '@/services/monitorService'
import { parseBulkLegs } from '@/utils/legParser'
import type { ParsedLeg } from '@/utils/legParser'
import { useLTPStore } from '@store/ltpStore'

const STRATEGY_TYPES = [
  'IRON_CONDOR', 'STRADDLE', 'STRANGLE', 'BULL_CALL_SPREAD',
  'BEAR_PUT_SPREAD', 'IRON_FLY', 'COVERED_CALL', 'FUTURES', 'CUSTOM',
]

const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'NIFTYNXT50', 'MIDCPNIFTY']

const LOT_SIZES: Record<string, number> = {
  NIFTY: 75, BANKNIFTY: 30, FINNIFTY: 65, NIFTYNXT50: 25, MIDCPNIFTY: 50,
}

const EXCHANGE_DEFAULTS: Record<string, string> = {
  NIFTY: 'NFO', BANKNIFTY: 'NFO', FINNIFTY: 'NFO', NIFTYNXT50: 'NFO', MIDCPNIFTY: 'NFO',
}

// Quick strategy templates — only fills structure, not prices
const STRATEGY_TEMPLATES: Record<string, Array<Partial<LegDraft>>> = {
  STRADDLE: [
    { side: 'SELL', option_type: 'CE', quantity: '1' },
    { side: 'SELL', option_type: 'PE', quantity: '1' },
  ],
  IRON_CONDOR: [
    { side: 'SELL', option_type: 'CE', quantity: '1' },
    { side: 'SELL', option_type: 'PE', quantity: '1' },
    { side: 'BUY',  option_type: 'CE', quantity: '1' },
    { side: 'BUY',  option_type: 'PE', quantity: '1' },
  ],
  IRON_FLY: [
    { side: 'SELL', option_type: 'CE', quantity: '1' },
    { side: 'SELL', option_type: 'PE', quantity: '1' },
    { side: 'BUY',  option_type: 'CE', quantity: '1' },
    { side: 'BUY',  option_type: 'PE', quantity: '1' },
  ],
  STRANGLE: [
    { side: 'SELL', option_type: 'CE', quantity: '1' },
    { side: 'SELL', option_type: 'PE', quantity: '1' },
  ],
  BULL_CALL_SPREAD: [
    { side: 'BUY',  option_type: 'CE', quantity: '1' },
    { side: 'SELL', option_type: 'CE', quantity: '1' },
  ],
  BEAR_PUT_SPREAD: [
    { side: 'BUY',  option_type: 'PE', quantity: '1' },
    { side: 'SELL', option_type: 'PE', quantity: '1' },
  ],
}

interface LegDraft {
  id: string
  side: 'BUY' | 'SELL'
  instrument: string
  underlying: string
  strike: string
  option_type: 'CE' | 'PE' | 'FUT'
  expiry: string
  quantity: string
  lot_size: number
  entry_price: string
}

function mkLeg(underlying: string, lotSize: number, overrides?: Partial<LegDraft>): LegDraft {
  return {
    id: crypto.randomUUID(),
    side: 'SELL',
    instrument: '',
    underlying,
    strike: '',
    option_type: 'CE',
    expiry: '',
    quantity: '1',
    lot_size: lotSize,
    entry_price: '',
    ...overrides,
  }
}

function fromParsed(p: ParsedLeg, lotSize: number): LegDraft {
  return {
    id: crypto.randomUUID(),
    side: p.side,
    instrument: p.instrument,
    underlying: p.underlying,
    strike: p.strike !== null ? String(p.strike) : '',
    option_type: p.option_type,
    expiry: p.expiry,
    quantity: String(p.quantity),
    lot_size: p.lot_size ?? lotSize,
    entry_price: p.entry_price !== null ? String(p.entry_price) : '',
  }
}

const fieldCls = 'bg-surface-3 border border-border-default text-text-primary text-xs rounded px-2 py-1.5 focus:outline-none focus:border-accent-blue w-full'
const labelCls = 'block text-[10px] text-text-muted mb-1 uppercase tracking-wide'

interface Props {
  onClose: () => void
  onSaved: (monitorId: string) => void
}

export function AddMonitoredPositionForm({ onClose, onSaved }: Props) {
  const qc = useQueryClient()
  const ltpMap = useLTPStore(s => s.ltpMap)

  // Form state
  const [name, setName] = useState('')
  const [strategyType, setStrategyType] = useState('IRON_CONDOR')
  const [underlying, setUnderlying] = useState('NIFTY')
  const [exchange, setExchange] = useState('NFO')
  const [notes, setNotes] = useState('')
  const [activeTab, setActiveTab] = useState<'manual' | 'bulk'>('manual')
  const [legs, setLegs] = useState<LegDraft[]>([mkLeg('NIFTY', 75)])

  // Bulk import state
  const [bulkText, setBulkText] = useState('')
  const [bulkErrors, setBulkErrors] = useState<Array<{ line: number; message: string }>>([])
  const [bulkSuccess, setBulkSuccess] = useState(false)

  // Fetch expiries for the selected underlying
  const { data: expiries = [] } = useQuery<string[]>({
    queryKey: ['expiries', underlying],
    queryFn: () =>
      axios.get<{ expiries: string[] }>(`/api/expiry/${underlying}`, { params: { exchange: 'NFO' } })
        .then(r => r.data.expiries ?? []),
    staleTime: 60_000,
  })

  const lotSize = LOT_SIZES[underlying] ?? 1

  const createMut = useMutation({
    mutationFn: (_saveAndAddAlerts: boolean) => {
      const payload = {
        name: name.trim() || `${underlying} ${strategyType}`,
        strategy_type: strategyType,
        underlying: underlying.toUpperCase(),
        exchange,
        notes: notes.trim() || undefined,
        legs: legs.map((l, i) => ({
          leg_number: i + 1,
          instrument: l.instrument || `${underlying}${l.expiry.replace(/-/g,'')}${l.option_type}${l.strike}`,
          underlying: l.underlying || underlying,
          strike: l.strike ? parseFloat(l.strike) : null,
          option_type: l.option_type,
          expiry: l.expiry,
          side: l.side,
          quantity: parseInt(l.quantity) || 1,
          lot_size: l.lot_size,
          entry_price: parseFloat(l.entry_price) || 0,
        })),
      }
      return monitorService.create(payload)
    },
    onSuccess: (data, _saveAndAddAlerts) => {
      qc.invalidateQueries({ queryKey: ['monitored-positions'] })
      toast.success('Position saved')
      onSaved(data.monitor_id)
    },
    onError: () => toast.error('Failed to save position'),
  })

  const handleUnderlyingChange = (u: string) => {
    setUnderlying(u)
    setExchange(EXCHANGE_DEFAULTS[u] ?? 'NFO')
    const ls = LOT_SIZES[u] ?? 1
    setLegs(lgs => lgs.map(l => ({ ...l, underlying: u, lot_size: ls })))
  }

  const applyTemplate = (type: string) => {
    const tpl = STRATEGY_TEMPLATES[type]
    if (!tpl) return
    const ls = LOT_SIZES[underlying] ?? 1
    setLegs(tpl.map(t => mkLeg(underlying, ls, t as Partial<LegDraft>)))
    setStrategyType(type)
  }

  const addLeg = () => {
    if (legs.length >= 10) { toast.error('Maximum 10 legs'); return }
    setLegs(lgs => [...lgs, mkLeg(underlying, lotSize)])
  }

  const updateLeg = useCallback((id: string, patch: Partial<LegDraft>) => {
    setLegs(lgs => lgs.map(l => l.id === id ? { ...l, ...patch } : l))
  }, [])

  const removeLeg = (id: string) => {
    setLegs(lgs => lgs.filter(l => l.id !== id))
  }

  const handleParse = () => {
    setBulkErrors([])
    setBulkSuccess(false)
    const result = parseBulkLegs(bulkText, underlying, lotSize)
    if (result.errors.length > 0) {
      setBulkErrors(result.errors)
      return
    }
    if (result.legs.length === 0) {
      setBulkErrors([{ line: 0, message: 'No legs parsed. Please check your input.' }])
      return
    }
    setLegs(result.legs.map(p => fromParsed(p, lotSize)))
    setBulkSuccess(true)
    setActiveTab('manual')
    toast.success(`${result.legs.length} legs parsed successfully`)
  }

  // Compute live MTM preview
  const previewMtm = legs.reduce((total, leg) => {
    const ltp = ltpMap[leg.instrument]?.tick.ltp
    if (!ltp || !leg.entry_price) return total
    const qty = (parseInt(leg.quantity) || 1) * leg.lot_size
    const entry = parseFloat(leg.entry_price)
    return total + (leg.side === 'SELL' ? (entry - ltp) * qty : (ltp - entry) * qty)
  }, 0)

  const netPremium = legs.reduce((s, l) => {
    const p = parseFloat(l.entry_price) || 0
    const q = (parseInt(l.quantity) || 1) * l.lot_size
    return s + (l.side === 'SELL' ? p * q : -p * q)
  }, 0)

  return (
    <div className="fixed inset-0 z-50 flex">
      {/* Overlay */}
      <div className="flex-1 bg-black/40" onClick={onClose} />
      {/* Panel */}
      <div className="w-[560px] bg-surface-1 border-l border-border-default flex flex-col h-full shadow-modal">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle shrink-0">
          <h2 className="text-sm font-semibold text-text-primary">Add Monitored Position</h2>
          <button onClick={onClose} className="p-1 text-text-muted hover:text-text-primary"><X size={16} /></button>
        </div>

        <div className="flex-1 overflow-y-auto custom-scroll p-5 space-y-5">

          {/* Details */}
          <section className="space-y-3">
            <h3 className="text-[10px] text-text-muted uppercase tracking-wide font-medium">Details</h3>
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label className={labelCls}>Name</label>
                <input value={name} onChange={e => setName(e.target.value)} placeholder={`${underlying} ${strategyType}`} className={fieldCls} />
              </div>
              <div>
                <label className={labelCls}>Strategy Type</label>
                <select value={strategyType} onChange={e => setStrategyType(e.target.value)} className={fieldCls}>
                  {STRATEGY_TYPES.map(t => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}
                </select>
              </div>
              <div>
                <label className={labelCls}>Notes</label>
                <input value={notes} onChange={e => setNotes(e.target.value)} placeholder="e.g. Entered on AngelOne 10:32 AM" className={fieldCls} />
              </div>
            </div>
          </section>

          {/* Instrument */}
          <section className="space-y-3">
            <h3 className="text-[10px] text-text-muted uppercase tracking-wide font-medium">Instrument</h3>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={labelCls}>Underlying</label>
                <select value={underlying} onChange={e => handleUnderlyingChange(e.target.value)} className={fieldCls}>
                  {UNDERLYINGS.map(u => <option key={u} value={u}>{u}</option>)}
                </select>
              </div>
              <div>
                <label className={labelCls}>Exchange</label>
                <input value={exchange} onChange={e => setExchange(e.target.value)} className={fieldCls} />
              </div>
            </div>
          </section>

          {/* Quick templates */}
          <section className="space-y-2">
            <h3 className="text-[10px] text-text-muted uppercase tracking-wide font-medium">Quick Templates (fills structure only)</h3>
            <div className="flex flex-wrap gap-1.5">
              {Object.keys(STRATEGY_TEMPLATES).map(t => (
                <button
                  key={t}
                  onClick={() => applyTemplate(t)}
                  className="px-2.5 py-1 text-[11px] border border-border-default text-text-secondary hover:border-accent-blue hover:text-accent-blue rounded transition-colors"
                >
                  {t.replace(/_/g, ' ')}
                </button>
              ))}
            </div>
          </section>

          {/* Tabs */}
          <section>
            <div className="flex items-center gap-1 mb-3 border-b border-border-subtle">
              {(['manual', 'bulk'] as const).map(tab => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={cn(
                    'px-3 py-1.5 text-xs -mb-px border-b-2 transition-colors',
                    activeTab === tab
                      ? 'border-accent-blue text-accent-blue'
                      : 'border-transparent text-text-muted hover:text-text-secondary'
                  )}
                >
                  {tab === 'manual' ? 'Add Manually' : 'Bulk Import'}
                </button>
              ))}
            </div>

            {activeTab === 'manual' && (
              <div className="space-y-3">
                {bulkSuccess && (
                  <div className="flex items-center gap-2 text-xs text-profit bg-profit/10 border border-profit/20 rounded px-3 py-2">
                    <CheckCircle2 size={13} />
                    {legs.length} legs parsed successfully — review and edit below
                  </div>
                )}
                {legs.map((leg, i) => (
                  <div key={leg.id} className="bg-surface-2 border border-border-subtle rounded-lg p-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] text-text-muted font-medium">Leg {i + 1}</span>
                      {legs.length > 1 && (
                        <button onClick={() => removeLeg(leg.id)} className="text-text-muted hover:text-loss">
                          <Trash2 size={12} />
                        </button>
                      )}
                    </div>
                    <div className="grid grid-cols-4 gap-2">
                      <div>
                        <label className={labelCls}>Side</label>
                        <select value={leg.side} onChange={e => updateLeg(leg.id, { side: e.target.value as 'BUY' | 'SELL' })} className={fieldCls}>
                          <option>BUY</option><option>SELL</option>
                        </select>
                      </div>
                      <div>
                        <label className={labelCls}>Type</label>
                        <select value={leg.option_type} onChange={e => updateLeg(leg.id, { option_type: e.target.value as 'CE' | 'PE' | 'FUT' })} className={fieldCls}>
                          <option>CE</option><option>PE</option><option>FUT</option>
                        </select>
                      </div>
                      <div>
                        <label className={labelCls}>Strike</label>
                        <input type="number" value={leg.strike} onChange={e => updateLeg(leg.id, { strike: e.target.value })} placeholder="24700" className={fieldCls} />
                      </div>
                      <div>
                        <label className={labelCls}>Qty (lots)</label>
                        <input type="number" min="1" value={leg.quantity} onChange={e => updateLeg(leg.id, { quantity: e.target.value })} className={fieldCls} />
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className={labelCls}>Expiry</label>
                        {expiries.length > 0 ? (
                          <select value={leg.expiry} onChange={e => updateLeg(leg.id, { expiry: e.target.value })} className={fieldCls}>
                            <option value="">Select expiry…</option>
                            {expiries.map(e => <option key={e} value={e}>{e}</option>)}
                          </select>
                        ) : (
                          <input value={leg.expiry} onChange={e => updateLeg(leg.id, { expiry: e.target.value })} placeholder="27-MAR-26" className={fieldCls} />
                        )}
                      </div>
                      <div>
                        <label className={labelCls}>Entry Price</label>
                        <input type="number" step="0.05" min="0" value={leg.entry_price}
                          onChange={e => updateLeg(leg.id, { entry_price: e.target.value })}
                          placeholder="98.50"
                          className={fieldCls}
                        />
                        {ltpMap[leg.instrument] && (
                          <p className="text-[10px] text-text-muted mt-0.5">
                            LTP: {ltpMap[leg.instrument]?.tick.ltp.toFixed(2)}
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
                <button
                  onClick={addLeg}
                  className="flex items-center gap-1.5 text-xs text-accent-blue hover:underline"
                >
                  <Plus size={12} /> Add Leg
                  {legs.length > 0 && <span className="text-text-muted">({legs.length}/10)</span>}
                </button>
              </div>
            )}

            {activeTab === 'bulk' && (
              <div className="space-y-3">
                <div className="text-xs text-text-muted space-y-1">
                  <p>Paste legs below. Supports 3 formats:</p>
                  <div className="bg-surface-2 border border-border-subtle rounded p-2 font-mono text-[11px] space-y-0.5">
                    <div className="text-text-muted">Format 1: SIDE STRIKE TYPE EXPIRY QTY PRICE</div>
                    <div className="text-text-secondary">SELL 24700 CE 27-MAR-26 1 98.50</div>
                    <div className="text-text-secondary">BUY  24800 CE 27-MAR-26 1 55.00</div>
                  </div>
                  <div className="bg-surface-2 border border-border-subtle rounded p-2 font-mono text-[11px] space-y-0.5">
                    <div className="text-text-muted">Format 2: SIDE FULL_SYMBOL QTY PRICE</div>
                    <div className="text-text-secondary">SELL NIFTY27MAR26CE24700 1 98.50</div>
                  </div>
                  <p className="text-text-muted">Omit price to auto-fetch LTP. Lines starting with # are ignored.</p>
                </div>
                <textarea
                  value={bulkText}
                  onChange={e => setBulkText(e.target.value)}
                  placeholder="SELL 24700 CE 27-MAR-26 1 98.50&#10;SELL 24500 PE 27-MAR-26 1 92.00&#10;BUY  24800 CE 27-MAR-26 1 55.00&#10;BUY  24400 PE 27-MAR-26 1 48.50"
                  rows={8}
                  className="w-full bg-surface-3 border border-border-default text-text-primary text-xs font-mono rounded px-3 py-2 focus:outline-none focus:border-accent-blue resize-none custom-scroll"
                />
                {bulkErrors.length > 0 && (
                  <div className="space-y-1">
                    {bulkErrors.map(err => (
                      <div key={err.line} className="flex items-start gap-2 text-xs text-loss bg-loss/5 border border-loss/20 rounded px-3 py-1.5">
                        <AlertCircle size={12} className="shrink-0 mt-0.5" />
                        {err.line > 0 && <span className="font-medium">Line {err.line}:</span>}
                        <span>{err.message}</span>
                      </div>
                    ))}
                  </div>
                )}
                <button
                  onClick={handleParse}
                  className="px-4 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
                >
                  Parse Legs
                </button>
              </div>
            )}
          </section>

          {/* Summary */}
          <section className="bg-surface-2 border border-border-subtle rounded-lg p-3">
            <div className="flex items-center justify-between text-xs">
              <span className="text-text-muted">Legs: <span className="text-text-primary">{legs.length}</span></span>
              <span className="text-text-muted">Net premium: <span className={cn('tabular-nums', netPremium >= 0 ? 'text-profit' : 'text-loss')}>₹{Math.abs(netPremium).toFixed(2)} {netPremium >= 0 ? 'received' : 'paid'}</span></span>
              {previewMtm !== 0 && (
                <span className="text-text-muted">Live MTM: <span className={cn('tabular-nums', previewMtm < 0 ? 'text-loss' : 'text-profit')}>₹{Math.abs(previewMtm).toFixed(0)}</span></span>
              )}
            </div>
          </section>

        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-border-subtle shrink-0">
          <button onClick={onClose} className="px-4 py-1.5 text-xs text-text-muted hover:text-text-primary border border-border-default rounded-md transition-colors">
            Cancel
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={() => createMut.mutate(false)}
              disabled={createMut.isPending || legs.length === 0}
              className="px-4 py-1.5 text-xs border border-border-default text-text-secondary rounded-md hover:bg-surface-2 transition-colors disabled:opacity-40"
            >
              Save Only
            </button>
            <button
              onClick={() => createMut.mutate(true)}
              disabled={createMut.isPending || legs.length === 0}
              className="px-4 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors disabled:opacity-40"
            >
              {createMut.isPending ? 'Saving…' : 'Save & Add Alerts →'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
