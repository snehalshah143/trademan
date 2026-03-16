import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'
import { persist } from 'zustand/middleware'
import type { Strategy, StrategyLeg, StrategyStatus } from '@/types/domain'

// ─── Types ───────────────────────────────────────────────────────────────────

type DraftStrategy = Omit<Strategy, 'id' | 'createdAt' | 'updatedAt'>

interface StrategyState {
  strategies: Strategy[]
  draftStrategy: DraftStrategy | null
  selectedStrategyId: string | null

  // Strategy CRUD
  addStrategy: (strategy: Strategy) => void
  updateStrategy: (id: string, updates: Partial<Strategy>) => void
  removeStrategy: (id: string) => void
  setStrategyStatus: (id: string, status: StrategyStatus) => void
  updateMTM: (id: string, mtm: number) => void

  // Draft management
  setDraft: (draft: DraftStrategy | null) => void
  updateDraft: (updates: Partial<DraftStrategy>) => void
  clearDraft: () => void

  // Leg management
  addLeg: (strategyId: string, leg: StrategyLeg) => void
  updateLeg: (strategyId: string, legId: string, updates: Partial<StrategyLeg>) => void
  removeLeg: (strategyId: string, legId: string) => void

  // Selection
  selectStrategy: (id: string | null) => void
  getStrategy: (id: string) => Strategy | undefined
  getActiveStrategies: () => Strategy[]
}

// ─── Store ───────────────────────────────────────────────────────────────────

export const useStrategyStore = create<StrategyState>()(
  persist(
    immer((set, get) => ({
      strategies: [],
      draftStrategy: null,
      selectedStrategyId: null,

      addStrategy: (strategy: Strategy) => {
        set((state) => {
          state.strategies.push(strategy)
        })
      },

      updateStrategy: (id: string, updates: Partial<Strategy>) => {
        set((state) => {
          const idx = state.strategies.findIndex((s) => s.id === id)
          if (idx !== -1) {
            Object.assign(state.strategies[idx], {
              ...updates,
              updatedAt: new Date().toISOString(),
            })
          }
        })
      },

      removeStrategy: (id: string) => {
        set((state) => {
          state.strategies = state.strategies.filter((s) => s.id !== id)
          if (state.selectedStrategyId === id) {
            state.selectedStrategyId = null
          }
        })
      },

      setStrategyStatus: (id: string, status: StrategyStatus) => {
        set((state) => {
          const strategy = state.strategies.find((s) => s.id === id)
          if (strategy) {
            strategy.status = status
            strategy.updatedAt = new Date().toISOString()
            if (status === 'ACTIVE') {
              strategy.entryTime = new Date().toISOString()
            } else if (status === 'CLOSED') {
              strategy.exitTime = new Date().toISOString()
            }
          }
        })
      },

      updateMTM: (id: string, mtm: number) => {
        set((state) => {
          const strategy = state.strategies.find((s) => s.id === id)
          if (strategy) {
            strategy.currentMTM = mtm
            if (strategy.peakProfit === undefined || mtm > strategy.peakProfit) {
              strategy.peakProfit = mtm
            }
            if (strategy.peakLoss === undefined || mtm < strategy.peakLoss) {
              strategy.peakLoss = mtm
            }
          }
        })
      },

      setDraft: (draft: DraftStrategy | null) => {
        set((state) => {
          state.draftStrategy = draft
        })
      },

      updateDraft: (updates: Partial<DraftStrategy>) => {
        set((state) => {
          if (state.draftStrategy) {
            Object.assign(state.draftStrategy, updates)
          }
        })
      },

      clearDraft: () => {
        set((state) => {
          state.draftStrategy = null
        })
      },

      addLeg: (strategyId: string, leg: StrategyLeg) => {
        set((state) => {
          const strategy = state.strategies.find((s) => s.id === strategyId)
          if (strategy) {
            strategy.legs.push(leg)
            strategy.updatedAt = new Date().toISOString()
          }
        })
      },

      updateLeg: (strategyId: string, legId: string, updates: Partial<StrategyLeg>) => {
        set((state) => {
          const strategy = state.strategies.find((s) => s.id === strategyId)
          if (strategy) {
            const leg = strategy.legs.find((l) => l.id === legId)
            if (leg) {
              Object.assign(leg, updates)
              strategy.updatedAt = new Date().toISOString()
            }
          }
        })
      },

      removeLeg: (strategyId: string, legId: string) => {
        set((state) => {
          const strategy = state.strategies.find((s) => s.id === strategyId)
          if (strategy) {
            strategy.legs = strategy.legs.filter((l) => l.id !== legId)
            strategy.updatedAt = new Date().toISOString()
          }
        })
      },

      selectStrategy: (id: string | null) => {
        set((state) => {
          state.selectedStrategyId = id
        })
      },

      getStrategy: (id: string) => {
        return get().strategies.find((s) => s.id === id)
      },

      getActiveStrategies: () => {
        return get().strategies.filter((s) => s.status === 'ACTIVE')
      },
    })),
    {
      name: 'trademan-strategies',
      // Only persist strategies and draft — not UI selection state
      partialize: (state) => ({
        strategies: state.strategies,
        draftStrategy: state.draftStrategy,
      }),
    }
  )
)
