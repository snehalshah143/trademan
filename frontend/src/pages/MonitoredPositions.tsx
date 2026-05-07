import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Eye, Search } from 'lucide-react'
import { cn } from '@/lib/utils'
import { monitorService } from '@/services/monitorService'
import type { MonitoredPosition } from '@/services/monitorService'
import { MonitoredPositionCard } from '@/components/Monitor/MonitoredPositionCard'
import { AddMonitoredPositionForm } from '@/components/Monitor/AddMonitoredPositionForm'
import { EditEntryPricesForm } from '@/components/Monitor/EditEntryPricesForm'
import { AlertList } from '@/components/AlertManager/AlertList'
import * as Dialog from '@radix-ui/react-dialog'

export function MonitoredPositions() {
  const [showAdd, setShowAdd] = useState(false)
  const [editPricesFor, setEditPricesFor] = useState<MonitoredPosition | null>(null)
  const [alertsFor, setAlertsFor] = useState<MonitoredPosition | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('ACTIVE')
  const [search, setSearch] = useState('')

  const { data: positions = [], isLoading, refetch } = useQuery({
    queryKey: ['monitored-positions', statusFilter],
    queryFn: () => monitorService.listAll(statusFilter ? { status: statusFilter } : {}),
    staleTime: 10_000,
    refetchInterval: 30_000,
  })

  const filtered = positions.filter(p =>
    p.name.toLowerCase().includes(search.toLowerCase()) ||
    p.underlying.toLowerCase().includes(search.toLowerCase())
  )

  const handleSaved = (monitorId: string) => {
    setShowAdd(false)
    refetch()
    // Optionally open alerts for the new position
    setTimeout(() => {
      const pos = positions.find(p => p.monitor_id === monitorId)
      if (pos) setAlertsFor(pos)
    }, 500)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-border-subtle bg-surface-1 shrink-0">
        <div className="flex items-center gap-3">
          <Eye size={18} className="text-accent-blue" />
          <div>
            <h1 className="text-sm font-bold text-text-primary">Monitored Positions</h1>
            <p className="text-xs text-text-muted">Positions entered manually for live monitoring</p>
          </div>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-accent-blue hover:bg-blue-500 text-white rounded-md transition-colors"
        >
          <Plus size={13} />
          Add Position
        </button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-border-subtle bg-surface-1 shrink-0">
        <div className="relative flex-1 max-w-sm">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search positions…"
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-surface-2 border border-border-default rounded text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue"
          />
        </div>
        <div className="flex items-center gap-1 border border-border-default rounded overflow-hidden">
          {['', 'ACTIVE', 'PAUSED', 'CLOSED'].map(s => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={cn(
                'px-3 py-1.5 text-xs transition-colors',
                statusFilter === s
                  ? 'bg-accent-blue text-white'
                  : 'text-text-secondary hover:bg-surface-2'
              )}
            >
              {s || 'All'}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto custom-scroll p-6">
        {isLoading ? (
          <div className="text-center py-16 text-xs text-text-muted">Loading positions…</div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Eye size={32} className="text-text-muted opacity-30 mb-3" />
            <p className="text-sm text-text-secondary">No monitored positions</p>
            <p className="text-xs text-text-muted mt-1">
              {search ? 'No positions match your search.' : 'Add positions you executed on your broker to monitor them live.'}
            </p>
            {!search && (
              <button
                onClick={() => setShowAdd(true)}
                className="mt-4 px-4 py-2 text-xs font-medium border border-accent-blue text-accent-blue rounded-md hover:bg-accent-blue/10 transition-colors"
              >
                Add first position
              </button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 max-w-4xl">
            {filtered.map(pos => (
              <MonitoredPositionCard
                key={pos.monitor_id}
                position={pos}
                onManageAlerts={setAlertsFor}
                onEditPrices={setEditPricesFor}
              />
            ))}
          </div>
        )}
      </div>

      {/* Add position panel */}
      {showAdd && (
        <AddMonitoredPositionForm
          onClose={() => setShowAdd(false)}
          onSaved={handleSaved}
        />
      )}

      {/* Edit entry prices modal */}
      {editPricesFor && (
        <EditEntryPricesForm
          position={editPricesFor}
          onClose={() => setEditPricesFor(null)}
        />
      )}

      {/* Manage alerts dialog */}
      <Dialog.Root open={!!alertsFor} onOpenChange={open => { if (!open) setAlertsFor(null) }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[560px] max-w-[95vw] h-[580px] bg-surface-1 border border-border-default rounded-xl shadow-modal overflow-hidden flex flex-col">
            <Dialog.Title className="sr-only">Alert Rules — {alertsFor?.name}</Dialog.Title>
            {alertsFor && (
              <AlertList
                strategyId={alertsFor.monitor_id}
                strategyName={alertsFor.name}
                positionLegs={alertsFor.legs.map(l => ({
                  leg_id: l.leg_id,
                  symbol: l.instrument,
                  side: l.side,
                }))}
              />
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  )
}
