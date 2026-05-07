import { useState, useRef, useEffect } from 'react'
import { createPortal } from 'react-dom'
import * as Dialog from '@radix-ui/react-dialog'
import { X, Loader2, ChevronDown } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useManualPositionStore } from '@store/manualPositionStore'
import { positionService } from '@/services/positionService'
import { cn } from '@/lib/utils'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
}

const EXCHANGES = ['NFO', 'NSE', 'BSE', 'BFO', 'MCX']
const PRODUCTS  = ['MIS', 'NRML', 'CNC']

function SymbolCombobox({
  value,
  exchange,
  onChange,
}: {
  value: string
  exchange: string
  onChange: (v: string) => void
}) {
  const [open,         setOpen]         = useState(false)
  const [query,        setQuery]        = useState(value)
  const [activeIdx,    setActiveIdx]    = useState(0)
  const [dropStyle,    setDropStyle]    = useState<React.CSSProperties>({})
  const [liveResults,  setLiveResults]  = useState<{ symbol: string; exchange: string }[]>([])
  const [liveLoading,  setLiveLoading]  = useState(false)
  const inputRef  = useRef<HTMLInputElement>(null)
  const listRef   = useRef<HTMLUListElement>(null)
  const timerRef  = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Sync local query when parent resets (e.g. exchange change clears symbol)
  useEffect(() => { setQuery(value) }, [value])

  // Cached full instrument list (warm from App startup prefetch)
  const { data: symbolList = [], isLoading: listLoading } = useQuery({
    queryKey: ['symbol-list', exchange],
    queryFn:  () => positionService.listSymbols(exchange),
    staleTime: 6 * 60 * 60 * 1000,
    gcTime:    24 * 60 * 60 * 1000,
  })

  const cacheReady = symbolList.length > 0

  // When cache is warm → filter locally (instant).
  // When cache is empty → fire debounced live search API (≥2 chars).
  const q = query.trim().toUpperCase()

  useEffect(() => {
    if (cacheReady) return          // cache takes over; no live search needed
    if (timerRef.current) clearTimeout(timerRef.current)
    if (q.length < 2) { setLiveResults([]); return }

    timerRef.current = setTimeout(async () => {
      setLiveLoading(true)
      try {
        const results = await positionService.searchSymbols(query.trim(), exchange)
        setLiveResults(results)
        setActiveIdx(0)
      } catch { setLiveResults([]) }
      finally { setLiveLoading(false) }
    }, 300)

    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [q, exchange, cacheReady]) // eslint-disable-line react-hooks/exhaustive-deps

  // Clear live results when exchange changes
  useEffect(() => { setLiveResults([]) }, [exchange])

  const suggestions = cacheReady
    ? (q.length > 0
        ? symbolList.filter(s => s.symbol.includes(q)).slice(0, 50)
        : symbolList.slice(0, 50))
    : liveResults.slice(0, 50)

  const isLoading = listLoading || liveLoading

  function recalcDropPos() {
    if (inputRef.current) {
      const r = inputRef.current.getBoundingClientRect()
      setDropStyle({ position: 'fixed', top: r.bottom + 4, left: r.left, width: r.width, zIndex: 9999 })
    }
  }

  function openDrop() {
    recalcDropPos()
    setOpen(true)
    setActiveIdx(0)
  }

  function select(sym: string) {
    onChange(sym)
    setQuery(sym)
    setOpen(false)
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!open) { openDrop(); return }
      const next = Math.min(activeIdx + 1, suggestions.length - 1)
      setActiveIdx(next)
      ;(listRef.current?.children[next] as HTMLElement | undefined)?.scrollIntoView({ block: 'nearest' })
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      const prev = Math.max(activeIdx - 1, 0)
      setActiveIdx(prev)
      ;(listRef.current?.children[prev] as HTMLElement | undefined)?.scrollIntoView({ block: 'nearest' })
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (open && suggestions[activeIdx]) select(suggestions[activeIdx].symbol)
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  // Hint shown when cache is empty and user hasn't typed enough yet
  const hint = !cacheReady && q.length < 2
    ? (listLoading ? 'Loading symbol list…' : 'Type at least 2 characters to search')
    : null

  const dropdown = (
    <ul
      ref={listRef}
      data-symbol-dropdown
      style={{ ...dropStyle, maxHeight: 220, overflowY: 'auto' }}
      className="bg-surface-2 border border-border-default rounded-lg shadow-modal custom-scroll"
    >
      {hint ? (
        <li className="px-3 py-3 text-xs text-text-muted text-center">{hint}</li>
      ) : isLoading ? (
        <li className="px-3 py-3 text-xs text-text-muted text-center flex items-center justify-center gap-2">
          <Loader2 size={12} className="animate-spin" /> Searching…
        </li>
      ) : suggestions.length === 0 ? (
        <li className="px-3 py-3 text-xs text-text-muted text-center">No symbols found</li>
      ) : suggestions.map((s, i) => (
        <li
          key={s.symbol}
          onMouseDown={e => { e.preventDefault(); select(s.symbol) }}
          onMouseEnter={() => setActiveIdx(i)}
          className={cn(
            'px-3 py-2 text-xs cursor-pointer flex items-center justify-between select-none',
            i === activeIdx ? 'bg-accent-blue/20 text-text-primary' : 'text-text-primary hover:bg-surface-3'
          )}
        >
          <span className="font-mono font-medium">{s.symbol}</span>
          <span className="text-text-muted text-[10px] ml-2">{s.exchange}</span>
        </li>
      ))}
    </ul>
  )

  return (
    <div className="relative">
      <input
        ref={inputRef}
        className="input-field uppercase pr-7 w-full"
        placeholder={listLoading ? 'Loading symbols…' : cacheReady ? 'Type to filter…' : 'Type 2+ chars to search…'}
        value={query}
        onChange={e => { setQuery(e.target.value.toUpperCase()); setActiveIdx(0); if (!open) openDrop() }}
        onFocus={openDrop}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={handleKeyDown}
        autoComplete="off"
        spellCheck={false}
      />
      <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-text-muted">
        {isLoading ? <Loader2 size={12} className="animate-spin" /> : <ChevronDown size={12} />}
      </span>
      {open && createPortal(dropdown, document.body)}
    </div>
  )
}

export function AddManualPositionModal({ open, onOpenChange }: Props) {
  const add = useManualPositionStore((s) => s.add)

  const [symbol,   setSymbol]   = useState('')
  const [exchange, setExchange] = useState('NFO')
  const [qty,      setQty]      = useState('')
  const [avgPrice, setAvgPrice] = useState('')
  const [product,  setProduct]  = useState('MIS')

  // Reset symbol when exchange changes (symbol from NFO is invalid on NSE)
  function handleExchangeChange(ex: string) {
    setExchange(ex)
    setSymbol('')
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!symbol.trim() || !qty) return
    const qtyNum = Number(qty)
    const avg    = Number(avgPrice) || 0
    add({
      symbol:   symbol.trim().toUpperCase(),
      exchange,
      qty:      qtyNum,
      buy_avg:  qtyNum > 0 ? avg : 0,
      sell_avg: qtyNum < 0 ? avg : 0,
      product,
    })
    setSymbol(''); setQty(''); setAvgPrice('')
    onOpenChange(false)
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
        <Dialog.Content
          className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[420px] max-w-[95vw] bg-surface-1 border border-border-default rounded-xl shadow-modal p-5"
          onInteractOutside={(e) => {
            if ((e.target as Element).closest('[data-symbol-dropdown]')) e.preventDefault()
          }}
        >
          <div className="flex items-center justify-between mb-4">
            <Dialog.Title className="text-sm font-semibold text-text-primary">Add Manual Position</Dialog.Title>
            <Dialog.Close className="text-text-muted hover:text-text-primary">
              <X size={16} />
            </Dialog.Close>
          </div>

          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            {/* Exchange first so autocomplete searches the right exchange */}
            <div className="grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-muted">Exchange</span>
                <select className="input-field" value={exchange} onChange={e => handleExchangeChange(e.target.value)}>
                  {EXCHANGES.map(ex => <option key={ex}>{ex}</option>)}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-muted">Symbol *</span>
                <SymbolCombobox value={symbol} exchange={exchange} onChange={setSymbol} />
              </label>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-muted">Qty * <span className="text-text-muted font-normal">(+ve BUY, -ve SELL)</span></span>
                <input
                  type="number"
                  className="input-field"
                  placeholder="e.g. 50 or -50"
                  value={qty}
                  onChange={e => setQty(e.target.value)}
                  required
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-muted">Avg Price</span>
                <input
                  type="number" step="0.05"
                  className="input-field"
                  placeholder="0.00"
                  value={avgPrice}
                  onChange={e => setAvgPrice(e.target.value)}
                />
              </label>
            </div>

            <label className="flex flex-col gap-1">
              <span className="text-xs text-text-muted">Product</span>
              <select className="input-field" value={product} onChange={e => setProduct(e.target.value)}>
                {PRODUCTS.map(p => <option key={p}>{p}</option>)}
              </select>
            </label>

            <div className="flex justify-end gap-2 mt-1">
              <Dialog.Close className="px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary border border-border-default rounded-md">
                Cancel
              </Dialog.Close>
              <button
                type="submit"
                className="px-4 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
              >
                Add Position
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
