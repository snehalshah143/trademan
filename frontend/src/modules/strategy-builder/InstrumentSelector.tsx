import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { ChevronDown, Search } from 'lucide-react'
import { useLTPStore } from '@store/ltpStore'
import { useInstruments } from '@hooks/useInstruments'
import { STATIC_INSTRUMENTS, type InstrumentInfo } from '@/data/instruments'
import { fmtPrice, cn } from '@/lib/utils'

// Keep this export for backward compat (StrategyBuilder imports it)
export const INSTRUMENTS = STATIC_INSTRUMENTS.filter((i) => i.category === 'INDEX').map((i) => ({
  symbol: i.symbol,
  label: i.symbol,
  lotSize: i.lotSize,
}))

// ─── Props ────────────────────────────────────────────────────────────────────

interface InstrumentSelectorProps {
  selected: string
  onChange: (symbol: string, lotSize: number) => void
  hasLegs?: boolean
}

// ─── Component ────────────────────────────────────────────────────────────────

export function InstrumentSelector({ selected, onChange, hasLegs = false }: InstrumentSelectorProps) {
  const { instruments, indexInstruments, stockInstruments } = useInstruments()
  const ltpMap = useLTPStore((s) => s.ltpMap)

  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [focusedIdx, setFocusedIdx] = useState(0)
  const searchRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const selectedInst = useMemo(
    () => instruments.find((i) => i.symbol === selected) ?? STATIC_INSTRUMENTS[0],
    [instruments, selected]
  )

  const entry = ltpMap[selected]
  const ltp = entry?.tick.ltp
  const change = entry?.tick.change ?? 0
  const changePct = entry?.tick.changePct ?? 0
  const isUp = change >= 0

  // ─── Filter instruments ──────────────────────────────────────────────────

  const filteredIndex = useMemo(() => {
    if (!query) return indexInstruments
    const q = query.toLowerCase()
    return indexInstruments.filter(
      (i) => i.symbol.toLowerCase().includes(q) || i.fullName.toLowerCase().includes(q)
    )
  }, [indexInstruments, query])

  const filteredStocks = useMemo(() => {
    if (!query) return stockInstruments
    const q = query.toLowerCase()
    return stockInstruments.filter(
      (i) => i.symbol.toLowerCase().includes(q) || i.fullName.toLowerCase().includes(q)
    )
  }, [stockInstruments, query])

  const flatList = useMemo(
    () => [...filteredIndex, ...filteredStocks],
    [filteredIndex, filteredStocks]
  )

  // Focus search on open
  useEffect(() => {
    if (open) {
      setTimeout(() => searchRef.current?.focus(), 50)
      setFocusedIdx(0)
    } else {
      setQuery('')
    }
  }, [open])

  // Scroll focused item into view
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${focusedIdx}"]`) as HTMLElement | null
    el?.scrollIntoView({ block: 'nearest' })
  }, [focusedIdx])

  const handleSelect = useCallback((inst: InstrumentInfo) => {
    if (inst.symbol === selected) {
      setOpen(false)
      return
    }
    if (hasLegs) {
      if (!confirm(`Change instrument to ${inst.symbol}? This will clear all legs.`)) return
    }
    onChange(inst.symbol, inst.lotSize)
    setOpen(false)
  }, [selected, hasLegs, onChange])

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setFocusedIdx((i) => Math.min(i + 1, flatList.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setFocusedIdx((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (flatList[focusedIdx]) handleSelect(flatList[focusedIdx])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        {/* ── Trigger button ─────────────────────────────────────────────── */}
        <button className="w-full flex items-center gap-2 px-4 py-2.5 bg-surface-1 border-b border-border-subtle hover:bg-surface-2 transition-colors text-left">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-text-primary">{selectedInst.symbol}</span>
              <span className="text-xs text-text-muted">(Lot size: {selectedInst.lotSize})</span>
            </div>
            {ltp ? (
              <div className="flex items-baseline gap-1.5 mt-0.5">
                <span className="text-xs font-mono text-text-primary">{fmtPrice(ltp)}</span>
                <span className={`text-[11px] font-mono ${isUp ? 'text-profit' : 'text-loss'}`}>
                  {isUp ? '+' : ''}{fmtPrice(change, 2)} ({isUp ? '+' : ''}{changePct.toFixed(2)}%)
                </span>
              </div>
            ) : (
              <span className="text-[11px] text-text-muted">—</span>
            )}
          </div>
          <ChevronDown
            size={14}
            className={cn('text-text-muted transition-transform shrink-0', open && 'rotate-180')}
          />
        </button>
      </Popover.Trigger>

      <Popover.Portal>
        <Popover.Content
          className="w-[380px] bg-surface-1 border border-border-default rounded-lg shadow-modal z-50 overflow-hidden animate-fade-in"
          align="start"
          sideOffset={2}
          onKeyDown={handleKeyDown}
        >
          {/* Search input */}
          <div className="flex items-center gap-2 px-3 py-2.5 border-b border-border-subtle">
            <Search size={13} className="text-text-muted shrink-0" />
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => { setQuery(e.target.value); setFocusedIdx(0) }}
              placeholder="Search instrument…"
              className="flex-1 bg-transparent text-sm text-text-primary placeholder-text-muted outline-none"
            />
            {query && (
              <button onClick={() => setQuery('')} className="text-text-muted hover:text-text-primary text-xs">
                ×
              </button>
            )}
          </div>

          {/* Instrument list */}
          <div ref={listRef} className="max-h-[400px] overflow-y-auto custom-scroll py-1">
            {/* Index section */}
            {filteredIndex.length > 0 && (
              <>
                <div className="px-3 py-1.5 text-[10px] font-medium text-text-muted uppercase tracking-widest">
                  Index — ({filteredIndex.length})
                </div>
                {filteredIndex.map((inst) => {
                  const flatIdx = flatList.indexOf(inst)
                  return (
                    <InstrumentRow
                      key={inst.symbol}
                      inst={inst}
                      isSelected={inst.symbol === selected}
                      isFocused={focusedIdx === flatIdx}
                      dataIdx={flatIdx}
                      ltpEntry={ltpMap[inst.symbol]}
                      onSelect={() => handleSelect(inst)}
                      onMouseEnter={() => setFocusedIdx(flatIdx)}
                    />
                  )
                })}
              </>
            )}

            {/* Stocks section */}
            {filteredStocks.length > 0 && (
              <>
                <div className="px-3 py-1.5 text-[10px] font-medium text-text-muted uppercase tracking-widest mt-1 border-t border-border-subtle">
                  Stocks — ({filteredStocks.length})
                </div>
                {filteredStocks.map((inst) => {
                  const flatIdx = flatList.indexOf(inst)
                  return (
                    <InstrumentRow
                      key={inst.symbol}
                      inst={inst}
                      isSelected={inst.symbol === selected}
                      isFocused={focusedIdx === flatIdx}
                      dataIdx={flatIdx}
                      ltpEntry={ltpMap[inst.symbol]}
                      onSelect={() => handleSelect(inst)}
                      onMouseEnter={() => setFocusedIdx(flatIdx)}
                    />
                  )
                })}
              </>
            )}

            {flatList.length === 0 && (
              <div className="px-3 py-6 text-center text-xs text-text-muted">
                No instruments match "{query}"
              </div>
            )}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

// ─── Instrument row ───────────────────────────────────────────────────────────

interface InstrumentRowProps {
  inst: InstrumentInfo
  isSelected: boolean
  isFocused: boolean
  dataIdx: number
  ltpEntry: { tick: { ltp: number; changePct: number } } | undefined
  onSelect: () => void
  onMouseEnter: () => void
}

function InstrumentRow({ inst, isSelected, isFocused, dataIdx, ltpEntry, onSelect, onMouseEnter }: InstrumentRowProps) {
  const ltp = ltpEntry?.tick.ltp
  const changePct = ltpEntry?.tick.changePct ?? 0
  const isUp = changePct >= 0

  return (
    <button
      data-idx={dataIdx}
      onClick={onSelect}
      onMouseEnter={onMouseEnter}
      className={cn(
        'w-full flex items-center gap-2 pl-3 pr-3 py-2 transition-colors text-left border-l-2',
        isSelected
          ? 'border-l-accent-blue'
          : 'border-l-transparent',
        isFocused && !isSelected
          ? 'bg-surface-3'
          : !isSelected
          ? 'hover:bg-surface-3'
          : ''
      )}
    >
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-bold text-text-primary leading-tight">{inst.symbol}</div>
        <div className="text-[11px] text-text-muted truncate">{inst.fullName}</div>
      </div>
      {ltp ? (
        <div className="text-right shrink-0">
          <div className="text-xs font-mono text-text-primary">{fmtPrice(ltp)}</div>
          <div className={`text-[10px] font-mono ${isUp ? 'text-profit' : 'text-loss'}`}>
            {isUp ? '+' : ''}{changePct.toFixed(2)}%
          </div>
        </div>
      ) : (
        <div className="text-right shrink-0">
          <span className="text-[10px] text-text-muted">Lot: {inst.lotSize}</span>
        </div>
      )}
    </button>
  )
}
