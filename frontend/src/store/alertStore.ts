import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'
import type { AlertEvent, AlertSeverity } from '@/types/domain'

// ─── Types ───────────────────────────────────────────────────────────────────

interface AlertState {
  events: AlertEvent[]
  isPanelOpen: boolean
  unreadCount: number
  cooldowns: Record<string, number>   // ruleId → unix ms expiry
  maxEvents: number                   // cap stored events

  // Actions
  addEvent: (event: AlertEvent) => void
  acknowledgeEvent: (id: string) => void
  acknowledgeAll: () => void
  clearEvents: () => void
  clearStrategyEvents: (strategyId: string) => void

  // Panel
  openPanel: () => void
  closePanel: () => void
  togglePanel: () => void

  // Cooldown helpers
  isOnCooldown: (ruleId: string) => boolean
  setCooldown: (ruleId: string, durationMs: number) => void

  // Selectors
  getBySeverity: (severity: AlertSeverity) => AlertEvent[]
  getByStrategy: (strategyId: string) => AlertEvent[]
  getUnacknowledged: () => AlertEvent[]
}

// ─── Store ───────────────────────────────────────────────────────────────────

export const useAlertStore = create<AlertState>()(
  immer((set, get) => ({
    events: [],
    isPanelOpen: false,
    unreadCount: 0,
    cooldowns: {},
    maxEvents: 500,

    addEvent: (event: AlertEvent) => {
      // Reject if on cooldown
      if (get().isOnCooldown(event.ruleId)) return

      set((state) => {
        // Prepend (newest first), cap at maxEvents
        state.events.unshift(event)
        if (state.events.length > state.maxEvents) {
          state.events = state.events.slice(0, state.maxEvents)
        }
        if (!event.acknowledged) {
          state.unreadCount += 1
        }
      })
    },

    acknowledgeEvent: (id: string) => {
      set((state) => {
        const event = state.events.find((e) => e.id === id)
        if (event && !event.acknowledged) {
          event.acknowledged = true
          state.unreadCount = Math.max(0, state.unreadCount - 1)
        }
      })
    },

    acknowledgeAll: () => {
      set((state) => {
        state.events.forEach((e) => {
          e.acknowledged = true
        })
        state.unreadCount = 0
      })
    },

    clearEvents: () => {
      set((state) => {
        state.events = []
        state.unreadCount = 0
      })
    },

    clearStrategyEvents: (strategyId: string) => {
      set((state) => {
        const removed = state.events.filter((e) => e.strategyId === strategyId)
        const unacknowledgedRemoved = removed.filter((e) => !e.acknowledged).length
        state.events = state.events.filter((e) => e.strategyId !== strategyId)
        state.unreadCount = Math.max(0, state.unreadCount - unacknowledgedRemoved)
      })
    },

    openPanel: () => {
      set((state) => {
        state.isPanelOpen = true
      })
    },

    closePanel: () => {
      set((state) => {
        state.isPanelOpen = false
      })
    },

    togglePanel: () => {
      set((state) => {
        state.isPanelOpen = !state.isPanelOpen
      })
    },

    isOnCooldown: (ruleId: string) => {
      const expiry = get().cooldowns[ruleId]
      return expiry !== undefined && Date.now() < expiry
    },

    setCooldown: (ruleId: string, durationMs: number) => {
      set((state) => {
        state.cooldowns[ruleId] = Date.now() + durationMs
      })
    },

    getBySeverity: (severity: AlertSeverity) => {
      return get().events.filter((e) => e.severity === severity)
    },

    getByStrategy: (strategyId: string) => {
      return get().events.filter((e) => e.strategyId === strategyId)
    },

    getUnacknowledged: () => {
      return get().events.filter((e) => !e.acknowledged)
    },
  }))
)
