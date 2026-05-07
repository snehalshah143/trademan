import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { monitorService } from '@/services/monitorService'
import type { MonitoredPosition } from '@/services/monitorService'
import { useLTPStore } from '@store/ltpStore'

interface EditEntryPricesFormProps {
  position: MonitoredPosition
  onClose: () => void
}

export function EditEntryPricesForm({ position, onClose }: EditEntryPricesFormProps) {
  const qc = useQueryClient()
  const ltpMap = useLTPStore(s => s.ltpMap)

  const [prices, setPrices] = useState<Record<string, string>>(
    Object.fromEntries(position.legs.map(l => [l.leg_id, String(l.entry_price)]))
  )

  const updateMut = useMutation({
    mutationFn: async () => {
      for (const leg of position.legs) {
        const newPrice = parseFloat(prices[leg.leg_id] || '0')
        if (newPrice !== leg.entry_price && newPrice > 0) {
          await monitorService.updateLegPrice(position.monitor_id, leg.leg_id, newPrice)
        }
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['monitored-positions'] })
      toast.success('Entry prices updated')
      onClose()
    },
    onError: () => toast.error('Failed to update prices'),
  })

  const inputCls = 'bg-surface-3 border border-border-default text-text-primary text-sm rounded px-2 py-1.5 w-full tabular-nums focus:outline-none focus:border-accent-blue'

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-surface-1 border border-border-default rounded-xl shadow-modal w-full max-w-md flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle">
          <div>
            <h3 className="text-sm font-semibold text-text-primary">Edit Entry Prices</h3>
            <p className="text-xs text-text-muted mt-0.5">Correct your actual executed prices</p>
          </div>
        </div>

        <div className="p-5 space-y-4 overflow-y-auto custom-scroll max-h-[60vh]">
          {position.legs.map((leg, i) => {
            const ltp = ltpMap[leg.instrument]?.tick.ltp
            return (
              <div key={leg.leg_id}>
                <div className="text-xs text-text-secondary mb-1.5 font-medium">
                  Leg {i + 1}: {leg.instrument} <span className={leg.side === 'BUY' ? 'text-profit' : 'text-loss'}>{leg.side}</span>
                </div>
                <div className="flex items-center gap-3">
                  <div className="flex-1">
                    <label className="block text-[10px] text-text-muted mb-1">Entry Price</label>
                    <input
                      type="number"
                      step="0.05"
                      min="0"
                      value={prices[leg.leg_id] ?? ''}
                      onChange={e => setPrices(p => ({ ...p, [leg.leg_id]: e.target.value }))}
                      className={inputCls}
                    />
                  </div>
                  {ltp !== undefined && (
                    <div className="w-28">
                      <div className="text-[10px] text-text-muted mb-1">Current LTP</div>
                      <div className="text-sm tabular-nums text-text-secondary">{ltp.toFixed(2)}</div>
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-border-subtle">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-xs text-text-muted hover:text-text-primary border border-border-default rounded-md transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => updateMut.mutate()}
            disabled={updateMut.isPending}
            className="px-4 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors disabled:opacity-40"
          >
            {updateMut.isPending ? 'Updating…' : 'Update Prices'}
          </button>
        </div>
      </div>
    </div>
  )
}
