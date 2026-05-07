import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'
import type { LTPTick, WSConnectionStatus } from '@/types/domain'

// ─── Types ───────────────────────────────────────────────────────────────────

export type LTPDirection = 'up' | 'down' | 'flat'

export interface LTPEntry {
  tick: LTPTick
  direction: LTPDirection
  flashKey: number      // incremented on each update to re-trigger CSS animation
  lastUpdated: number   // unix ms
}

interface LTPState {
  // Data
  ltpMap: Record<string, LTPEntry>
  connectionStatus: WSConnectionStatus
  lastHeartbeat: number | null
  staleThresholdMs: number  // default 5000ms

  // Actions
  updateTick: (tick: LTPTick) => void
  updateBatch: (ticks: LTPTick[]) => void
  setConnectionStatus: (status: WSConnectionStatus) => void
  setHeartbeat: () => void
  getLTP: (symbol: string) => number | undefined
  getDirection: (symbol: string) => LTPDirection
  isStale: (symbol: string) => boolean
  isAnyStale: () => boolean
}

// ─── Store ───────────────────────────────────────────────────────────────────

export const useLTPStore = create<LTPState>()(
  immer((set, get) => ({
    ltpMap: {},
    connectionStatus: 'DISCONNECTED',
    lastHeartbeat: null,
    staleThresholdMs: 30000,

    updateTick: (tick: LTPTick) => {
      set((state) => {
        const existing = state.ltpMap[tick.symbol]
        let direction: LTPDirection = 'flat'
        if (existing) {
          if (tick.ltp > existing.tick.ltp) direction = 'up'
          else if (tick.ltp < existing.tick.ltp) direction = 'down'
        }
        state.ltpMap[tick.symbol] = {
          tick,
          direction,
          flashKey: (existing?.flashKey ?? 0) + 1,
          lastUpdated: Date.now(),
        }
      })
    },

    updateBatch: (ticks: LTPTick[]) => {
      set((state) => {
        for (const tick of ticks) {
          const existing = state.ltpMap[tick.symbol]
          let direction: LTPDirection = 'flat'
          if (existing) {
            if (tick.ltp > existing.tick.ltp) direction = 'up'
            else if (tick.ltp < existing.tick.ltp) direction = 'down'
          }
          state.ltpMap[tick.symbol] = {
            tick,
            direction,
            flashKey: (existing?.flashKey ?? 0) + 1,
            lastUpdated: Date.now(),
          }
        }
      })
    },

    setConnectionStatus: (status: WSConnectionStatus) => {
      set((state) => {
        state.connectionStatus = status
      })
    },

    setHeartbeat: () => {
      set((state) => {
        state.lastHeartbeat = Date.now()
      })
    },

    getLTP: (symbol: string) => {
      return get().ltpMap[symbol]?.tick.ltp
    },

    getDirection: (symbol: string) => {
      return get().ltpMap[symbol]?.direction ?? 'flat'
    },

    isStale: (symbol: string) => {
      const entry = get().ltpMap[symbol]
      if (!entry) return true
      return Date.now() - entry.lastUpdated > get().staleThresholdMs
    },

    isAnyStale: () => {
      const { ltpMap, staleThresholdMs } = get()
      const now = Date.now()
      return Object.values(ltpMap).some(
        (entry) => now - entry.lastUpdated > staleThresholdMs
      )
    },
  }))
)
