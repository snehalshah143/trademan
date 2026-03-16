import { useState } from 'react'
import { Plus } from 'lucide-react'
import { generateId } from '@/lib/utils'
import type { StrategyLeg, InstrumentType, OrderSide, ProductType, OrderType, Exchange } from '@/types/domain'

interface LegEditorProps {
  onAddLeg: (leg: StrategyLeg) => void
  defaultUnderlying?: string
}

const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY']

export function LegEditor({ onAddLeg, defaultUnderlying = 'NIFTY' }: LegEditorProps) {
  const [underlying, setUnderlying] = useState(defaultUnderlying)
  const [instrumentType, setInstrumentType] = useState<InstrumentType>('CE')
  const [side, setSide] = useState<OrderSide>('SELL')
  const [expiry, setExpiry] = useState('')
  const [strike, setStrike] = useState<number>(22000)
  const [lots, setLots] = useState(1)
  const [productType, setProductType] = useState<ProductType>('MIS')
  const [orderType, setOrderType] = useState<OrderType>('MARKET')

  const lotSizeMap: Record<string, number> = { NIFTY: 75, BANKNIFTY: 15, SENSEX: 10, FINNIFTY: 40, MIDCPNIFTY: 50 }
  const lotSize = lotSizeMap[underlying] ?? 1

  const handleAdd = () => {
    const symbol = instrumentType === 'FUT'
      ? `${underlying}FUT`
      : `${underlying}${expiry.replace(/-/g, '').slice(2)}${strike}${instrumentType}`

    const leg: StrategyLeg = {
      id: generateId('leg'),
      legIndex: 0,
      instrument: {
        symbol,
        exchange: 'NFO' as Exchange,
        instrumentType,
        expiry: expiry || undefined,
        strike: instrumentType !== 'FUT' ? strike : undefined,
        lotSize,
        tickSize: 0.05,
      },
      side,
      lots,
      quantity: lots * lotSize,
      productType,
      orderType,
      status: 'DRAFT',
      isHedge: side === 'SELL',
    }
    onAddLeg(leg)
  }

  const inputClass = 'w-full px-2.5 py-1.5 text-sm bg-surface-3 border border-border-default rounded-md text-text-primary focus:outline-none focus:border-accent-blue'
  const labelClass = 'text-xs text-text-muted mb-1 block'

  return (
    <div className="bg-surface-2 border border-border-subtle rounded-md p-4">
      <div className="grid grid-cols-4 gap-3 mb-3">
        <div>
          <label className={labelClass}>Underlying</label>
          <select value={underlying} onChange={(e) => setUnderlying(e.target.value)} className={inputClass}>
            {UNDERLYINGS.map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
        </div>
        <div>
          <label className={labelClass}>Type</label>
          <select value={instrumentType} onChange={(e) => setInstrumentType(e.target.value as InstrumentType)} className={inputClass}>
            <option value="CE">CE</option>
            <option value="PE">PE</option>
            <option value="FUT">FUT</option>
          </select>
        </div>
        <div>
          <label className={labelClass}>Side</label>
          <select value={side} onChange={(e) => setSide(e.target.value as OrderSide)} className={inputClass}>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
        </div>
        <div>
          <label className={labelClass}>Lots</label>
          <input type="number" min={1} value={lots} onChange={(e) => setLots(Number(e.target.value))} className={inputClass} />
        </div>
      </div>

      <div className="grid grid-cols-4 gap-3 mb-3">
        <div>
          <label className={labelClass}>Expiry</label>
          <input type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} className={inputClass} />
        </div>
        <div>
          <label className={labelClass}>Strike</label>
          <input type="number" step={50} value={strike} onChange={(e) => setStrike(Number(e.target.value))} className={inputClass} />
        </div>
        <div>
          <label className={labelClass}>Product</label>
          <select value={productType} onChange={(e) => setProductType(e.target.value as ProductType)} className={inputClass}>
            <option value="MIS">MIS</option>
            <option value="NRML">NRML</option>
            <option value="CNC">CNC</option>
          </select>
        </div>
        <div>
          <label className={labelClass}>Order type</label>
          <select value={orderType} onChange={(e) => setOrderType(e.target.value as OrderType)} className={inputClass}>
            <option value="MARKET">MARKET</option>
            <option value="LIMIT">LIMIT</option>
          </select>
        </div>
      </div>

      <button
        onClick={handleAdd}
        className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
      >
        <Plus size={14} />
        Add Leg
      </button>
    </div>
  )
}
