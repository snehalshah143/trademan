import { useLTPStore } from '@store/ltpStore'
import { generateId } from '@/lib/utils'
import type { StrategyLeg, InstrumentType, OrderSide, Exchange } from '@/types/domain'

interface PresetButtonsProps {
  underlying: string
  onLoadPreset: (legs: StrategyLeg[]) => void
}

function makeOption(
  symbol: string,
  exchange: Exchange,
  instrumentType: InstrumentType,
  strike: number,
  expiry: string,
  side: OrderSide,
  lots: number,
  lotSize: number
): StrategyLeg {
  return {
    id: generateId('leg'),
    legIndex: 0,
    instrument: { symbol, exchange, instrumentType, strike, expiry, lotSize, tickSize: 0.05 },
    side,
    lots,
    quantity: lots * lotSize,
    productType: 'MIS',
    orderType: 'MARKET',
    status: 'DRAFT',
    isHedge: side === 'SELL',
  }
}

export function PresetButtons({ underlying, onLoadPreset }: PresetButtonsProps) {
  const ltp = useLTPStore((s) => s.getLTP(underlying)) ?? 22500
  const lotSize = { NIFTY: 25, BANKNIFTY: 15, SENSEX: 10, FINNIFTY: 40 }[underlying] ?? 25

  const atm = Math.round(ltp / 50) * 50
  const expiry = (() => {
    const d = new Date()
    // Next Thursday
    const day = d.getDay()
    const daysUntilThur = (4 - day + 7) % 7 || 7
    d.setDate(d.getDate() + daysUntilThur)
    return d.toISOString().slice(0, 10)
  })()

  const sym = (strike: number, type: InstrumentType, side: OrderSide, lots = 1) =>
    makeOption(`${underlying}${expiry.replace(/-/g, '').slice(2)}${strike}${type}`, 'NFO', type, strike, expiry, side, lots, lotSize)

  const PRESETS: Record<string, () => StrategyLeg[]> = {
    'Iron Condor': () => [
      sym(atm - 200, 'PE', 'SELL'), sym(atm - 400, 'PE', 'BUY'),
      sym(atm + 200, 'CE', 'SELL'), sym(atm + 400, 'CE', 'BUY'),
    ],
    'Straddle': () => [sym(atm, 'PE', 'SELL'), sym(atm, 'CE', 'SELL')],
    'Strangle': () => [sym(atm - 200, 'PE', 'SELL'), sym(atm + 200, 'CE', 'SELL')],
    'Bull Call': () => [sym(atm, 'CE', 'BUY'), sym(atm + 100, 'CE', 'SELL')],
    'Bear Put':  () => [sym(atm, 'PE', 'BUY'), sym(atm - 100, 'PE', 'SELL')],
    'Cvrd Call': () => [sym(atm, 'FUT' as InstrumentType, 'BUY'), sym(atm + 100, 'CE', 'SELL')],
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {Object.entries(PRESETS).map(([label, builder]) => (
        <button
          key={label}
          onClick={() => onLoadPreset(builder())}
          className="px-3 py-1 text-xs bg-surface-3 hover:bg-surface-4 border border-border-default text-text-secondary hover:text-text-primary rounded transition-colors"
        >
          {label}
        </button>
      ))}
    </div>
  )
}
