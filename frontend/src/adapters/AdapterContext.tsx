import React, { createContext, useContext, useEffect, useState } from 'react'
import { BrokerAdapter, OpenAlgoAdapter, MockAdapter } from './broker.adapter'
import type { BrokerConfig } from '@/types/domain'

// ─── Context ─────────────────────────────────────────────────────────────────

interface AdapterContextValue {
  adapter: BrokerAdapter
  config: BrokerConfig
  setConfig: (config: BrokerConfig) => void
  switchToMock: () => void
  switchToOpenAlgo: (apiKey: string, host: string, wsHost: string) => void
}

const AdapterContext = createContext<AdapterContextValue | null>(null)

const STORAGE_KEY = 'trademan-broker-config'

function loadStoredConfig(): BrokerConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw) as BrokerConfig
  } catch {
    // ignore
  }
  return { adapter: 'mock' }
}

function createAdapter(config: BrokerConfig): BrokerAdapter {
  if (config.adapter === 'openalgo' && config.apiKey && config.openalgoHost && config.openalgoWsHost) {
    return new OpenAlgoAdapter({
      apiKey: config.apiKey,
      host: config.openalgoHost,
      wsHost: config.openalgoWsHost,
    })
  }
  return new MockAdapter()
}

// ─── Provider ────────────────────────────────────────────────────────────────

export function AdapterProvider({ children }: { children: React.ReactNode }) {
  const [config, setConfigState] = useState<BrokerConfig>(loadStoredConfig)
  const [adapter, setAdapter] = useState<BrokerAdapter>(() => createAdapter(loadStoredConfig()))

  const setConfig = (newConfig: BrokerConfig) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(newConfig))
    setConfigState(newConfig)
    setAdapter(createAdapter(newConfig))
  }

  const switchToMock = () => {
    setConfig({ adapter: 'mock' })
  }

  const switchToOpenAlgo = (apiKey: string, host: string, wsHost: string) => {
    setConfig({
      adapter: 'openalgo',
      apiKey,
      openalgoHost: host,
      openalgoWsHost: wsHost,
    })
  }

  // Re-create adapter if config changes externally (e.g. storage event from other tabs)
  useEffect(() => {
    const handleStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY && e.newValue) {
        try {
          const newConfig = JSON.parse(e.newValue) as BrokerConfig
          setConfigState(newConfig)
          setAdapter(createAdapter(newConfig))
        } catch {
          // ignore
        }
      }
    }
    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [])

  return (
    <AdapterContext.Provider value={{ adapter, config, setConfig, switchToMock, switchToOpenAlgo }}>
      {children}
    </AdapterContext.Provider>
  )
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useAdapter(): AdapterContextValue {
  const ctx = useContext(AdapterContext)
  if (!ctx) {
    throw new Error('useAdapter must be used within AdapterProvider')
  }
  return ctx
}
