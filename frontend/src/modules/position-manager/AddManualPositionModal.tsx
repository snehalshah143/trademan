import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useManualPositionStore } from '@store/manualPositionStore'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
}

const EXCHANGES = ['NFO', 'NSE', 'BSE', 'BFO', 'MCX']
const PRODUCTS  = ['MIS', 'NRML', 'CNC']

export function AddManualPositionModal({ open, onOpenChange }: Props) {
  const add = useManualPositionStore((s) => s.add)

  const [symbol,   setSymbol]   = useState('')
  const [exchange, setExchange] = useState('NFO')
  const [qty,      setQty]      = useState('')
  const [buyAvg,   setBuyAvg]   = useState('')
  const [sellAvg,  setSellAvg]  = useState('')
  const [product,  setProduct]  = useState('MIS')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!symbol.trim() || !qty) return
    add({
      symbol:   symbol.trim().toUpperCase(),
      exchange,
      qty:      Number(qty),
      buy_avg:  Number(buyAvg) || 0,
      sell_avg: Number(sellAvg) || 0,
      product,
    })
    // reset
    setSymbol(''); setQty(''); setBuyAvg(''); setSellAvg('')
    onOpenChange(false)
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[420px] max-w-[95vw] bg-surface-1 border border-border-default rounded-xl shadow-modal p-5">
          <div className="flex items-center justify-between mb-4">
            <Dialog.Title className="text-sm font-semibold text-text-primary">Add Manual Position</Dialog.Title>
            <Dialog.Close className="text-text-muted hover:text-text-primary">
              <X size={16} />
            </Dialog.Close>
          </div>

          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-muted">Symbol *</span>
                <input
                  className="input-field uppercase"
                  placeholder="e.g. NIFTY24JAN22000CE"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value)}
                  required
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-muted">Exchange</span>
                <select className="input-field" value={exchange} onChange={(e) => setExchange(e.target.value)}>
                  {EXCHANGES.map(ex => <option key={ex}>{ex}</option>)}
                </select>
              </label>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-muted">Qty *</span>
                <input
                  type="number"
                  className="input-field"
                  placeholder="0"
                  value={qty}
                  onChange={(e) => setQty(e.target.value)}
                  required
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-muted">Buy Avg</span>
                <input
                  type="number"
                  step="0.05"
                  className="input-field"
                  placeholder="0.00"
                  value={buyAvg}
                  onChange={(e) => setBuyAvg(e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-muted">Sell Avg</span>
                <input
                  type="number"
                  step="0.05"
                  className="input-field"
                  placeholder="0.00"
                  value={sellAvg}
                  onChange={(e) => setSellAvg(e.target.value)}
                />
              </label>
            </div>

            <label className="flex flex-col gap-1">
              <span className="text-xs text-text-muted">Product</span>
              <select className="input-field" value={product} onChange={(e) => setProduct(e.target.value)}>
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
