import { useMemo } from 'react'
import { Layers } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import axios from 'axios'
import { useLTPStore } from '@store/ltpStore'
import { useQuote, spotExchangeFor } from '@hooks/useQuote'
import { getStaticInstrument } from '@/data/instruments'
import { generateId } from '@/lib/utils'
import { PrebuiltStrategyCard } from './PrebuiltStrategyCard'
import type { StrategyLeg, Exchange, InstrumentType, OrderSide } from '@/types/domain'
import type { MiniPayoffStrategy } from '@/components/charts/MiniPayoffSVG'

interface ChainRow { strike: number; callLTP: number; putLTP: number }
interface ChainResp { rows: ChainRow[]; atm_strike: number | null }

interface PrebuiltStrategiesPanelProps {
  underlying: string
  lotSize: number
  expiry: string
  selectedExpiry?: string | null
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
  selectedExpiry,
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
  const { data: quoteData } = useQuote(underlying, spotExchangeFor(underlying))
  const spot = quoteData?.ltp ?? ltpMap[underlying]?.tick.ltp ?? 0
  const atm  = spot > 0 ? getATM(spot, interval) : 0

  // Fetch expiries independently — needed when user hasn't clicked any expiry tab yet
  const { data: expiriesData } = useQuery<{ expiries: string[] }>({
    queryKey: ['expiries', underlying, 'NFO'],
    queryFn: () =>
      axios
        .get<{ expiries: string[] }>(`/api/instruments/expiries?symbol=${underlying}&exchange=NFO`)
        .then((r) => r.data),
    staleTime: 60_000,
  })

  // Resolve expiry: prefer selectedExpiry (from option chain tab) → leg expiry → first from API
  const chainExpiry = selectedExpiry || expiry || expiriesData?.expiries?.[0] || ''

  // Fetch option chain to get real LTPs per strike
  const { data: chainData } = useQuery<ChainResp>({
    queryKey: ['optionchain', underlying, 'NFO', chainExpiry, 50],
    queryFn: () =>
      axios
        .get<ChainResp>(`/api/instruments/optionchain?symbol=${underlying}&exchange=NFO&expiry=${chainExpiry}&strike_count=50`)
        .then((r) => r.data),
    staleTime: 5_000,
    enabled: !!chainExpiry,
  })

  const chainRows = useMemo(() => chainData?.rows ?? [], [chainData])

  // Prefer chain's own atm_strike; fall back to local calculation
  const chainAtm = chainData?.atm_strike ?? 0
  const effectiveAtm = chainAtm > 0 ? chainAtm : atm

  function getChainLTP(strike: number, type: 'CE' | 'PE'): number {
    const row = chainRows.find((r) => r.strike === strike)
    if (!row) return 0
    return type === 'CE' ? (row.callLTP ?? 0) : (row.putLTP ?? 0)
  }

  function buildLegs(key: MiniPayoffStrategy): StrategyLeg[] {
    if (effectiveAtm === 0) return []
    if (!chainExpiry) return []   // no expiry yet — wait for API

    const A = effectiveAtm
    const L = (strike: number, type: InstrumentType, side: OrderSide): StrategyLeg => {
      const ltp = (type === 'CE' || type === 'PE') ? getChainLTP(strike, type) : 0
      return makeLeg(underlying, chainExpiry, strike, type, side, 1, lotSize, ltp)
    }

    switch (key) {
      case 'straddle':
        return [L(A, 'CE', 'SELL'), L(A, 'PE', 'SELL')]
      case 'strangle':
        return [L(A + 2 * interval, 'CE', 'SELL'), L(A - 2 * interval, 'PE', 'SELL')]
      case 'bullcall':
        return [L(A, 'CE', 'BUY'), L(A + 2 * interval, 'CE', 'SELL')]
      case 'bearput':
        return [L(A, 'PE', 'BUY'), L(A - 2 * interval, 'PE', 'SELL')]
      case 'ironfly':
        return [
          L(A, 'CE', 'SELL'), L(A, 'PE', 'SELL'),
          L(A + 2 * interval, 'CE', 'BUY'), L(A - 2 * interval, 'PE', 'BUY'),
        ]
      case 'ironcondor':
        return [
          L(A + 2 * interval, 'CE', 'SELL'), L(A - 2 * interval, 'PE', 'SELL'),
          L(A + 4 * interval, 'CE', 'BUY'), L(A - 4 * interval, 'PE', 'BUY'),
        ]
      case 'coveredcall': {
        const futLtp = ltpMap[`${underlying}FUT`]?.tick.ltp ?? 0
        return [
          makeLeg(underlying, chainExpiry, 0, 'FUT', 'BUY', 1, lotSize, futLtp),
          L(A + 2 * interval, 'CE', 'SELL'),
        ]
      }
      case 'longcall':
        return [L(A, 'CE', 'BUY')]
      default:
        return []
    }
  }

  const notReady = effectiveAtm === 0 || !chainExpiry

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
          {effectiveAtm > 0 && chainExpiry && (
            <span className="ml-2 text-text-secondary normal-case font-normal">
              ATM {effectiveAtm} · Lot {lotSize}
            </span>
          )}
        </div>
        <div className="grid grid-cols-4 gap-2">
          {PREBUILT_CARDS.map(({ name, key }) => (
            <PrebuiltStrategyCard
              key={key}
              name={name}
              strategy={key}
              disabled={notReady}
              onClick={() => {
                const legs = buildLegs(key).map((l, i) => ({ ...l, legIndex: i }))
                if (legs.length > 0) onLoadPreset(legs)
              }}
            />
          ))}
        </div>
        {notReady && (
          <p className="text-xs text-text-muted mt-2 text-center">
            {spot === 0 ? 'Loading spot price…' : 'Loading expiry data…'}
          </p>
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
