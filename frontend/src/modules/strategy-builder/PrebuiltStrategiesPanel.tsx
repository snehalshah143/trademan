import { Layers } from 'lucide-react'
import { useLTPStore } from '@store/ltpStore'
import { useQuote, spotExchangeFor } from '@hooks/useQuote'
import { getStaticInstrument } from '@/data/instruments'
import { generateId } from '@/lib/utils'
import { PrebuiltStrategyCard } from './PrebuiltStrategyCard'
import type { StrategyLeg, Exchange, InstrumentType, OrderSide } from '@/types/domain'
import type { MiniPayoffStrategy } from '@/components/charts/MiniPayoffSVG'

interface PrebuiltStrategiesPanelProps {
  underlying: string
  lotSize: number
  expiry: string
  onLoadPreset: (legs: StrategyLeg[]) => void
  onBuildOptionChain: () => void
  onBuildLegEditor: () => void
}

function getATM(spot: number, interval: number): number {
  return Math.round(spot / interval) * interval
}

function makeLeg(
  underlying: string,
  expiry: string,
  strike: number,
  type: InstrumentType,
  side: OrderSide,
  lots: number,
  lotSize: number,
  ltp: number,
): StrategyLeg {
  const expiryShort = expiry ? expiry.replace(/-/g, '').slice(2) : ''
  const symbol = type === 'FUT'
    ? `${underlying}FUT`
    : `${underlying}${expiryShort}${strike}${type}`

  return {
    id: generateId('leg'),
    legIndex: 0,
    instrument: {
      symbol,
      exchange: 'NFO' as Exchange,
      instrumentType: type,
      expiry: expiry || undefined,
      strike: type !== 'FUT' ? strike : undefined,
      lotSize,
      tickSize: 0.05,
    },
    side,
    lots,
    quantity: lots * lotSize,
    productType: 'NRML',
    orderType: 'MARKET',
    entryPrice: ltp > 0 ? ltp : undefined,
    status: 'DRAFT',
    isHedge: side === 'SELL',
  }
}

const PREBUILT_CARDS: Array<{ name: string; key: MiniPayoffStrategy }> = [
  { name: 'Straddle',          key: 'straddle'    },
  { name: 'Strangle',          key: 'strangle'    },
  { name: 'Bull Call Spread',  key: 'bullcall'    },
  { name: 'Bear Put Spread',   key: 'bearput'     },
  { name: 'Iron Fly',          key: 'ironfly'     },
  { name: 'Iron Condor',       key: 'ironcondor'  },
  { name: 'Covered Call',      key: 'coveredcall' },
  { name: 'Long Call',         key: 'longcall'    },
]

export function PrebuiltStrategiesPanel({
  underlying,
  expiry,
  onLoadPreset,
  onBuildOptionChain,
  onBuildLegEditor,
}: PrebuiltStrategiesPanelProps) {
  const ltpMap = useLTPStore((s) => s.ltpMap)

  // Instrument metadata — correct for every underlying including stocks
  const inst     = getStaticInstrument(underlying)
  const lotSize  = inst?.lotSize  ?? 1
  const interval = inst?.strikeInterval ?? 50

  // Spot price: quote API is authoritative (covers stocks via NSE exchange)
  // Fall back to WS ltpMap for indices that are already subscribed
  const { data: quoteData } = useQuote(underlying, spotExchangeFor(underlying))
  const spot = quoteData?.ltp ?? ltpMap[underlying]?.tick.ltp ?? 0
  const atm  = spot > 0 ? getATM(spot, interval) : 0

  const getLTP = (sym: string) => ltpMap[sym]?.tick.ltp ?? 0

  function buildLegs(key: MiniPayoffStrategy): StrategyLeg[] {
    if (atm === 0) return []   // no spot price yet — don't build with wrong strikes

    const L = (strike: number, type: InstrumentType, side: OrderSide): StrategyLeg => {
      const exShort = expiry ? expiry.replace(/-/g, '').slice(2) : ''
      const sym = type === 'FUT' ? `${underlying}FUT` : `${underlying}${exShort}${strike}${type}`
      return makeLeg(underlying, expiry, strike, type, side, 1, lotSize, getLTP(sym))
    }

    switch (key) {
      case 'straddle':
        return [L(atm, 'CE', 'SELL'), L(atm, 'PE', 'SELL')]
      case 'strangle':
        return [L(atm + 2 * interval, 'CE', 'SELL'), L(atm - 2 * interval, 'PE', 'SELL')]
      case 'bullcall':
        return [L(atm, 'CE', 'BUY'), L(atm + 2 * interval, 'CE', 'SELL')]
      case 'bearput':
        return [L(atm, 'PE', 'BUY'), L(atm - 2 * interval, 'PE', 'SELL')]
      case 'ironfly':
        return [
          L(atm, 'CE', 'SELL'), L(atm, 'PE', 'SELL'),
          L(atm + 2 * interval, 'CE', 'BUY'), L(atm - 2 * interval, 'PE', 'BUY'),
        ]
      case 'ironcondor':
        return [
          L(atm + 2 * interval, 'CE', 'SELL'), L(atm - 2 * interval, 'PE', 'SELL'),
          L(atm + 4 * interval, 'CE', 'BUY'), L(atm - 4 * interval, 'PE', 'BUY'),
        ]
      case 'coveredcall': {
        const futSym = `${underlying}FUT`
        return [
          makeLeg(underlying, expiry, 0, 'FUT', 'BUY', 1, lotSize, getLTP(futSym)),
          L(atm + 2 * interval, 'CE', 'SELL'),
        ]
      }
      case 'longcall':
        return [L(atm, 'CE', 'BUY')]
      default:
        return []
    }
  }

  return (
    <div className="flex flex-col items-center py-6 px-4 gap-5">
      <div className="text-center">
        <div className="flex justify-center mb-3">
          <Layers size={28} className="text-text-muted opacity-40" />
        </div>
        <p className="text-sm text-text-secondary">No Position Found</p>
        <p className="text-xs text-text-muted mt-1">
          Select trades from Option Chain or use a Prebuilt strategy
        </p>
      </div>

      <div className="w-full">
        <div className="text-xs text-text-muted font-medium mb-3 uppercase tracking-wide">
          Prebuilt Strategies
          {spot > 0 && (
            <span className="ml-2 text-text-secondary normal-case font-normal">
              ATM {atm} · Lot {lotSize}
            </span>
          )}
        </div>
        <div className="grid grid-cols-4 gap-2">
          {PREBUILT_CARDS.map(({ name, key }) => (
            <PrebuiltStrategyCard
              key={key}
              name={name}
              strategy={key}
              onClick={() => {
                const legs = buildLegs(key).map((l, i) => ({ ...l, legIndex: i }))
                if (legs.length > 0) onLoadPreset(legs)
              }}
            />
          ))}
        </div>
        {spot === 0 && (
          <p className="text-xs text-text-muted mt-2 text-center">Loading spot price…</p>
        )}
      </div>

      <div className="flex gap-2 w-full">
        <button
          onClick={onBuildLegEditor}
          className="flex-1 py-2 text-xs border border-border-default text-text-secondary rounded-md hover:border-accent-blue hover:text-text-primary transition-colors"
        >
          Import from Leg Editor
        </button>
        <button
          onClick={onBuildOptionChain}
          className="flex-1 py-2 text-xs border border-border-default text-text-secondary rounded-md hover:border-accent-blue hover:text-text-primary transition-colors"
        >
          Build using Option Chain
        </button>
      </div>
    </div>
  )
}
