import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface ManualPosition {
  id:       string
  symbol:   string
  exchange: string
  qty:      number
  buy_avg:  number
  sell_avg: number
  product:  string
  addedAt:  string
}

interface ManualPositionState {
  positions: ManualPosition[]
  add:    (p: Omit<ManualPosition, 'id' | 'addedAt'>) => void
  remove: (id: string) => void
}

export const useManualPositionStore = create<ManualPositionState>()(
  persist(
    (set) => ({
      positions: [],
      add: (p) =>
        set((s) => ({
          positions: [
            ...s.positions,
            { ...p, id: crypto.randomUUID(), addedAt: new Date().toISOString() },
          ],
        })),
      remove: (id) =>
        set((s) => ({ positions: s.positions.filter((p) => p.id !== id) })),
    }),
    { name: 'manual-positions' }
  )
)
