import { useState } from 'react'
import { Eye, EyeOff, CheckCircle, XCircle, Loader, RefreshCw } from 'lucide-react'
import axios from 'axios'
import toast from 'react-hot-toast'
import { useAdapter } from '@adapters/AdapterContext'
import { MetricCard } from '@/components/ui/MetricCard'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { format } from 'date-fns'

interface HealthData {
  status: string
  broker_connected: boolean
  adapter: string
  redis: boolean
}

interface SyncLog {
  id: number
  operation: string
  status: string
  message: string | null
  records_synced: number
  started_at: string | null
  completed_at: string | null
  duration_ms: number | null
}

interface SyncStatus {
  stats: { instruments: number; expiries: number }
  logs: SyncLog[]
}

export function Settings() {
  const { config, switchToMock, switchToOpenAlgo } = useAdapter()
  const queryClient = useQueryClient()

  const [adapterType, setAdapterType] = useState<'mock' | 'openalgo'>(config.adapter)
  const [host, setHost] = useState(config.openalgoHost ?? 'http://localhost:5000')
  const [wsHost, setWsHost] = useState(config.openalgoWsHost ?? 'ws://localhost:8765')
  const [apiKey, setApiKey] = useState(config.apiKey ?? '')
  const [showKey, setShowKey] = useState(false)
  const [testStatus, setTestStatus] = useState<'idle' | 'loading' | 'ok' | 'fail'>('idle')
  const [testMessage, setTestMessage] = useState('')
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)

  const { data: health } = useQuery<HealthData>({
    queryKey: ['health'],
    queryFn: async () => (await axios.get('/api/health')).data,
    refetchInterval: 10000,
  })

  const { data: syncStatus, refetch: refetchSyncStatus } = useQuery<SyncStatus>({
    queryKey: ['syncStatus'],
    queryFn: async () => (await axios.get('/api/instruments/sync/status')).data,
    staleTime: 30_000,
  })

  const handleSync = async (scope: 'all' | 'expiries') => {
    setSyncing(true)
    try {
      await axios.post(`/api/instruments/sync?scope=${scope}`)
      toast.success(`Sync complete`)
      await refetchSyncStatus()
      queryClient.invalidateQueries({ queryKey: ['instruments'] })
      queryClient.invalidateQueries({ queryKey: ['expiries'] })
    } catch {
      toast.error('Sync failed')
    } finally {
      setSyncing(false)
    }
  }

  const handleTest = async () => {
    setTestStatus('loading')
    try {
      const res = await axios.post('/api/v1/settings/test', {
        host: host,
        api_key: apiKey,
      })
      setTestStatus(res.data.connected ? 'ok' : 'fail')
      setTestMessage(res.data.message || '')
      if (!res.data.connected) {
        toast.error(res.data.message || 'Connection failed')
      }
    } catch {
      setTestStatus('fail')
      toast.error('Could not reach backend')
    }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await axios.post('/api/v1/settings/broker', {
        adapter_type: adapterType,
        host: adapterType === 'openalgo' ? host : undefined,
        ws_host: adapterType === 'openalgo' ? wsHost : undefined,
        api_key: adapterType === 'openalgo' ? apiKey : undefined,
      })

      if (adapterType === 'mock') {
        switchToMock()
      } else {
        switchToOpenAlgo(apiKey, host, wsHost)
      }
      toast.success('Settings saved')
    } catch {
      toast.error('Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  const inputClass = 'w-full px-3 py-2 text-sm bg-surface-3 border border-border-default rounded-md text-text-primary focus:outline-none focus:border-accent-blue'
  const labelClass = 'text-xs text-text-muted mb-1 block'

  return (
    <div className="max-w-2xl mx-auto p-6 space-y-6">

      {/* Broker config card */}
      <div className="panel">
        <div className="panel-header">
          <h2 className="font-semibold text-text-primary">Broker Configuration</h2>
        </div>
        <div className="p-5 space-y-4">
          {/* Adapter toggle */}
          <div>
            <label className={labelClass}>Adapter type</label>
            <div className="flex gap-2">
              {(['mock', 'openalgo'] as const).map((a) => (
                <button
                  key={a}
                  onClick={() => setAdapterType(a)}
                  className={`px-4 py-2 text-sm rounded-md border transition-colors ${
                    adapterType === a
                      ? 'bg-accent-blue border-accent-blue text-white'
                      : 'bg-surface-3 border-border-default text-text-secondary hover:text-text-primary'
                  }`}
                >
                  {a === 'mock' ? 'Mock' : 'OpenAlgo'}
                </button>
              ))}
            </div>
          </div>

          {adapterType === 'openalgo' && (
            <>
              <div>
                <label className={labelClass}>OpenAlgo Host</label>
                <input value={host} onChange={(e) => setHost(e.target.value)} className={inputClass} placeholder="http://localhost:5000" />
              </div>
              <div>
                <label className={labelClass}>OpenAlgo WS Host</label>
                <input value={wsHost} onChange={(e) => setWsHost(e.target.value)} className={inputClass} placeholder="ws://localhost:8765" />
              </div>
              <div>
                <label className={labelClass}>API Key</label>
                <div className="relative">
                  <input
                    type={showKey ? 'text' : 'password'}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    className={`${inputClass} pr-9`}
                    placeholder="Enter API key"
                  />
                  <button
                    onClick={() => setShowKey((v) => !v)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary"
                  >
                    {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>
            </>
          )}

          <div className="flex items-center gap-3 pt-1">
            {adapterType === 'openalgo' && (
              <button
                onClick={handleTest}
                disabled={testStatus === 'loading'}
                className="px-4 py-2 text-sm bg-surface-3 hover:bg-surface-4 border border-border-default text-text-secondary rounded-md transition-colors flex items-center gap-2"
              >
                {testStatus === 'loading' && <Loader size={13} className="animate-spin" />}
                Test Connection
              </button>
            )}

            {testStatus === 'ok'   && <div className="flex items-center gap-1.5 text-sm text-profit"><CheckCircle size={14} /> Connected</div>}
            {testStatus === 'fail' && (
              <div className="flex items-center gap-1.5 text-sm text-loss">
                <XCircle size={14} />
                <span>{testMessage || 'Failed'}</span>
              </div>
            )}

            <button
              onClick={handleSave}
              disabled={saving}
              className="ml-auto px-4 py-2 text-sm font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors disabled:opacity-60 flex items-center gap-2"
            >
              {saving && <Loader size={13} className="animate-spin" />}
              Save
            </button>
          </div>
        </div>
      </div>

      {/* System status card */}
      <div className="panel">
        <div className="panel-header">
          <h2 className="font-semibold text-text-primary">System Status</h2>
        </div>
        <div className="p-5">
          <div className="grid grid-cols-2 gap-3">
            <MetricCard
              label="Backend"
              value={health?.status === 'ok' ? 'Healthy' : 'Unreachable'}
              valueClass={health?.status === 'ok' ? 'text-profit' : 'text-loss'}
              compact
            />
            <MetricCard
              label="Broker"
              value={health?.broker_connected ? 'Connected' : 'Disconnected'}
              valueClass={health?.broker_connected ? 'text-profit' : 'text-loss'}
              compact
            />
            <MetricCard
              label="Adapter"
              value={health?.adapter ?? '—'}
              compact
            />
            <MetricCard
              label="Redis"
              value={health?.redis ? 'Available' : 'Unavailable'}
              valueClass={health?.redis ? 'text-profit' : 'text-accent-amber'}
              compact
            />
          </div>
        </div>
      </div>

      {/* Instrument data card */}
      <div className="panel">
        <div className="panel-header">
          <h2 className="font-semibold text-text-primary">Instrument Data</h2>
          <p className="text-xs text-text-muted mt-0.5">
            F&O instrument catalogue and expiry dates cached from OpenAlgo
          </p>
        </div>
        <div className="p-5 space-y-4">

          {/* Cache stats */}
          <div className="grid grid-cols-2 gap-3">
            <MetricCard
              label="Instruments"
              value={syncStatus?.stats?.instruments != null ? String(syncStatus.stats.instruments) : '—'}
              compact
            />
            <MetricCard
              label="Expiries cached"
              value={syncStatus?.stats?.expiries != null ? String(syncStatus.stats.expiries) : '—'}
              compact
            />
          </div>

          {/* Sync actions */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => handleSync('all')}
              disabled={syncing}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors disabled:opacity-60"
            >
              {syncing ? <Loader size={13} className="animate-spin" /> : <RefreshCw size={13} />}
              Sync All
            </button>
            <button
              onClick={() => handleSync('expiries')}
              disabled={syncing}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-surface-3 hover:bg-surface-4 border border-border-default text-text-secondary rounded-md transition-colors disabled:opacity-60"
            >
              {syncing ? <Loader size={13} className="animate-spin" /> : <RefreshCw size={13} />}
              Sync Expiries
            </button>
            <button
              onClick={() => refetchSyncStatus()}
              className="ml-auto p-1.5 text-text-muted hover:text-text-secondary transition-colors"
              title="Refresh status"
            >
              <RefreshCw size={13} />
            </button>
          </div>

          {/* Sync log table */}
          {syncStatus?.logs && syncStatus.logs.length > 0 && (
            <div>
              <div className="text-xs text-text-muted mb-2">Recent syncs</div>
              <div className="border border-border-subtle rounded-md overflow-hidden">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-surface-2 text-[10px] text-text-muted uppercase border-b border-border-subtle">
                      <th className="px-3 py-1.5 text-left">Operation</th>
                      <th className="px-3 py-1.5 text-left">Status</th>
                      <th className="px-3 py-1.5 text-right">Records</th>
                      <th className="px-3 py-1.5 text-right hidden sm:table-cell">Duration</th>
                      <th className="px-3 py-1.5 text-right">Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {syncStatus.logs.map((log) => (
                      <tr key={log.id} className="border-b border-border-subtle last:border-b-0">
                        <td className="px-3 py-2 text-text-secondary font-mono">{log.operation}</td>
                        <td className="px-3 py-2">
                          <span className={
                            log.status === 'success' ? 'text-profit' :
                            log.status === 'partial' ? 'text-accent-amber' :
                            'text-loss'
                          }>
                            {log.status}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-text-secondary">
                          {log.records_synced}
                        </td>
                        <td className="px-3 py-2 text-right text-text-muted hidden sm:table-cell">
                          {log.duration_ms != null ? `${log.duration_ms}ms` : '—'}
                        </td>
                        <td className="px-3 py-2 text-right text-text-muted whitespace-nowrap">
                          {log.started_at
                            ? format(new Date(log.started_at), 'HH:mm:ss')
                            : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {syncStatus.logs[0]?.message && (
                <p className="text-[10px] text-text-muted mt-1.5 truncate" title={syncStatus.logs[0].message}>
                  Last: {syncStatus.logs[0].message}
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
