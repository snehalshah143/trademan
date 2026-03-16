import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X, CheckCircle, Loader } from 'lucide-react'
import toast from 'react-hot-toast'
import { useStrategyStore } from '@store/strategyStore'
import { useAdapter } from '@adapters/AdapterContext'
import { executeEntry } from '@/lib/execution'
import { generateId } from '@/lib/utils'
import type { Strategy, StrategyLeg } from '@/types/domain'

type StepState = 'pending' | 'running' | 'done' | 'error'

interface ExecuteModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  draft: Omit<Strategy, 'id' | 'createdAt' | 'updatedAt'>
}

export function ExecuteModal({ open, onOpenChange, draft }: ExecuteModalProps) {
  const { adapter } = useAdapter()
  const { addStrategy, updateLeg, setStrategyStatus } = useStrategyStore()

  const [strategyName, setStrategyName] = useState(draft.name || 'New Strategy')
  const [steps, setSteps] = useState<StepState[]>(['pending', 'pending', 'pending', 'pending'])
  const [running, setRunning] = useState(false)

  const buyLegs  = draft.legs.filter((l) => l.side === 'BUY')
  const sellLegs = draft.legs.filter((l) => l.side === 'SELL')

  const setStep = (i: number, state: StepState) =>
    setSteps((prev) => { const n = [...prev]; n[i] = state; return n })

  const stepIcon = (state: StepState) =>
    state === 'running' ? <Loader size={14} className="animate-spin text-accent-blue" /> :
    state === 'done'    ? <CheckCircle size={14} className="text-profit" /> :
    state === 'error'   ? <X size={14} className="text-loss" /> :
    <div className="w-3.5 h-3.5 rounded-full border border-border-default" />

  const handleExecute = async () => {
    setRunning(true)
    const now = new Date().toISOString()
    const strategyId = generateId('strat')

    const strategy: Strategy = {
      ...draft,
      id: strategyId,
      name: strategyName,
      status: 'PENDING_ENTRY',
      createdAt: now,
      updatedAt: now,
    }

    addStrategy(strategy)
    setStep(0, 'running')

    try {
      const result = await executeEntry(
        adapter,
        strategy,
        (legId, updates) => updateLeg(strategyId, legId, updates as Partial<StrategyLeg>)
      )

      setStep(0, result.success ? 'done' : 'error')
      setStep(1, 'done')
      setStep(2, result.success ? 'done' : 'error')

      if (result.success) {
        setStrategyStatus(strategyId, 'ACTIVE')
        setStep(3, 'done')
        toast.success(`Strategy "${strategyName}" is now active`)
        setTimeout(() => onOpenChange(false), 800)
      } else {
        setStrategyStatus(strategyId, 'ERROR')
        toast.error(result.error ?? 'Entry failed')
      }
    } catch {
      setStep(0, 'error')
      setStrategyStatus(strategyId, 'ERROR')
      toast.error('Execution failed')
    } finally {
      setRunning(false)
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-full max-w-md bg-surface-1 border border-border-default rounded-lg shadow-modal animate-fade-in">
          <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle">
            <Dialog.Title className="text-text-primary font-semibold">Execute Strategy</Dialog.Title>
            <Dialog.Close asChild>
              <button className="text-text-muted hover:text-text-primary"><X size={16} /></button>
            </Dialog.Close>
          </div>

          <div className="p-5">
            <div className="mb-4">
              <label className="text-xs text-text-muted mb-1 block">Strategy Name</label>
              <input
                value={strategyName}
                onChange={(e) => setStrategyName(e.target.value)}
                className="w-full px-3 py-2 text-sm bg-surface-3 border border-border-default rounded-md text-text-primary focus:outline-none focus:border-accent-blue"
              />
            </div>

            <div className="bg-surface-2 border border-border-subtle rounded-md px-4 py-3 mb-4 text-sm text-text-secondary">
              <div><span className="text-text-muted">Underlying:</span> {draft.underlyingSymbol}</div>
              <div><span className="text-text-muted">Legs:</span> {draft.legs.length} ({buyLegs.length} BUY, {sellLegs.length} SELL)</div>
            </div>

            <div className="space-y-2.5 mb-5">
              {[
                { label: `BUY legs (${buyLegs.length}) → place simultaneously`,                 idx: 0 },
                { label: 'Await fills — 500ms poll, 30s timeout',                              idx: 1 },
                { label: `SELL legs (${sellLegs.length}) → place simultaneously (if BUY ok)`, idx: 2 },
                { label: 'Strategy set to ACTIVE',                                              idx: 3 },
              ].map(({ label, idx }) => (
                <div key={idx} className="flex items-center gap-3 text-sm text-text-secondary">
                  {stepIcon(steps[idx])}
                  <span>{label}</span>
                </div>
              ))}
            </div>

            <div className="flex justify-end gap-3">
              <Dialog.Close asChild>
                <button disabled={running} className="px-4 py-2 text-sm text-text-secondary bg-surface-3 border border-border-default rounded-md disabled:opacity-50">
                  Cancel
                </button>
              </Dialog.Close>
              <button
                onClick={handleExecute}
                disabled={running}
                className="px-4 py-2 text-sm font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors disabled:opacity-60 flex items-center gap-2"
              >
                {running && <Loader size={13} className="animate-spin" />}
                {running ? 'Executing…' : 'Execute'}
              </button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
