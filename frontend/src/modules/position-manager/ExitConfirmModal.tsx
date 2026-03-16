import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X, CheckCircle, Loader } from 'lucide-react'
import toast from 'react-hot-toast'
import { useStrategyStore } from '@store/strategyStore'
import { useAdapter } from '@adapters/AdapterContext'
import { executeExit } from '@/lib/execution'
import type { Strategy, StrategyLeg } from '@/types/domain'

type StepState = 'pending' | 'running' | 'done' | 'error'

interface ExitConfirmModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  strategy: Strategy
  legIds?: string[]  // partial exit
}

export function ExitConfirmModal({ open, onOpenChange, strategy, legIds }: ExitConfirmModalProps) {
  const { adapter } = useAdapter()
  const { updateLeg, setStrategyStatus } = useStrategyStore()
  const [steps, setSteps] = useState<StepState[]>(['pending', 'pending', 'pending'])
  const [running, setRunning] = useState(false)

  const legsToExit = legIds
    ? strategy.legs.filter((l) => legIds.includes(l.id))
    : strategy.legs.filter((l) => l.status === 'FILLED')

  const sellLegs = legsToExit.filter((l) => l.side === 'SELL')
  const buyLegs  = legsToExit.filter((l) => l.side === 'BUY')

  const setStep = (i: number, state: StepState) => {
    setSteps((prev) => { const next = [...prev]; next[i] = state; return next })
  }

  const handleConfirm = async () => {
    setRunning(true)
    setSteps(['running', 'pending', 'pending'])

    try {
      const result = await executeExit(
        adapter,
        strategy,
        (legId, updates) => updateLeg(strategy.id, legId, updates as Partial<StrategyLeg>),
        legIds
      )

      setStep(0, 'done')
      setStep(1, 'done')
      setStep(2, 'done')

      if (result.success) {
        if (!legIds) setStrategyStatus(strategy.id, 'CLOSED')
        toast.success(`Exit complete — ${result.filledLegs.length} legs closed`)
        setTimeout(() => onOpenChange(false), 800)
      } else {
        toast.error(result.error ?? 'Exit partially failed')
      }
    } catch (err) {
      setStep(0, 'error')
      toast.error('Exit failed')
    } finally {
      setRunning(false)
    }
  }

  const stepLabel = (state: StepState) =>
    state === 'running' ? <Loader size={14} className="animate-spin text-accent-blue" /> :
    state === 'done'    ? <CheckCircle size={14} className="text-profit" /> :
    state === 'error'   ? <X size={14} className="text-loss" /> :
    <div className="w-3.5 h-3.5 rounded-full border border-border-default" />

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-full max-w-md bg-surface-1 border border-border-default rounded-lg shadow-modal animate-fade-in">
          <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle">
            <Dialog.Title className="text-text-primary font-semibold">Confirm Exit</Dialog.Title>
            <Dialog.Close asChild>
              <button className="text-text-muted hover:text-text-primary"><X size={16} /></button>
            </Dialog.Close>
          </div>

          <div className="p-5">
            <p className="text-sm text-text-secondary mb-4">
              Exiting <span className="text-text-primary font-medium">{strategy.name}</span>
              {legIds ? ` — ${legsToExit.length} selected leg(s)` : ' — all legs'}
            </p>

            <div className="space-y-2.5 mb-5">
              {[
                { label: `Step 1: Exit ${sellLegs.length} SELL leg(s) — buy back simultaneously`, idx: 0 },
                { label: 'Step 2: Await fills (500ms poll, 30s timeout)…',                        idx: 1 },
                { label: `Step 3: Exit ${buyLegs.length} BUY leg(s) — sell out simultaneously`,  idx: 2 },
              ].map(({ label, idx }) => (
                <div key={idx} className="flex items-center gap-3 text-sm text-text-secondary">
                  {stepLabel(steps[idx])}
                  <span>{label}</span>
                </div>
              ))}
            </div>

            <div className="flex justify-end gap-3">
              <Dialog.Close asChild>
                <button
                  disabled={running}
                  className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary bg-surface-3 border border-border-default rounded-md transition-colors disabled:opacity-50"
                >
                  Cancel
                </button>
              </Dialog.Close>
              <button
                onClick={handleConfirm}
                disabled={running}
                className="px-4 py-2 text-sm font-medium bg-red-600 hover:bg-red-500 text-white rounded-md transition-colors disabled:opacity-60 flex items-center gap-2"
              >
                {running && <Loader size={13} className="animate-spin" />}
                {running ? 'Exiting…' : 'Confirm Exit'}
              </button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
