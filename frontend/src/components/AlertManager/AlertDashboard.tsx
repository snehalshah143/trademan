import { useQuery } from '@tanstack/react-query'
import { Bell, TrendingUp, Activity, Clock } from 'lucide-react'
import { alertRuleService } from '@/services/alertService'
import { cn } from '@/lib/utils'

const SCOPE_COLORS: Record<string, string> = {
  STRATEGY: 'bg-accent-blue/20 text-accent-blue',
  LEG:      'bg-accent-purple/20 text-accent-purple',
  SPOT:     'bg-profit/20 text-profit',
  INDICATOR:'bg-accent-amber/20 text-accent-amber',
  MIXED:    'bg-text-muted/20 text-text-muted',
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export function AlertDashboard() {
  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['alert-stats'],
    queryFn: () => alertRuleService.getStats(),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const { data: history = [], isLoading: histLoading } = useQuery({
    queryKey: ['alert-history-recent'],
    queryFn: () => alertRuleService.getHistory({ limit: 10 }),
    staleTime: 15_000,
  })

  return (
    <div className="flex flex-col gap-6 p-6">

      {/* Stat cards */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: 'Total Alerts',    value: stats?.total_alerts,   icon: Bell,     color: 'text-text-primary' },
          { label: 'Active',          value: stats?.active_alerts,  icon: Activity, color: 'text-profit' },
          { label: 'Fired Today',     value: stats?.fired_today,    icon: Clock,    color: 'text-accent-amber' },
          { label: 'Fired This Week', value: stats?.fired_this_week,icon: TrendingUp,color: 'text-accent-blue' },
        ].map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="bg-surface-2 border border-border-subtle rounded-lg p-4">
            <div className="flex items-center gap-2 mb-2">
              <Icon size={14} className={cn('shrink-0', color)} />
              <span className="text-xs text-text-muted">{label}</span>
            </div>
            <div className={cn('text-2xl font-bold tabular-nums', color)}>
              {statsLoading ? '–' : (value ?? 0)}
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-6">

        {/* Recent fired alerts */}
        <div className="bg-surface-2 border border-border-subtle rounded-lg overflow-hidden">
          <div className="px-4 py-3 border-b border-border-subtle">
            <span className="text-xs font-medium text-text-primary uppercase tracking-wide">Recent Alerts</span>
          </div>
          <div className="divide-y divide-border-subtle">
            {histLoading ? (
              <div className="px-4 py-8 text-center text-xs text-text-muted">Loading…</div>
            ) : history.length === 0 ? (
              <div className="px-4 py-8 text-center text-xs text-text-muted">No alerts fired yet</div>
            ) : history.map(item => (
              <div key={item.id} className="px-4 py-2.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium text-text-primary truncate">{item.message}</p>
                    <p className="text-[10px] text-text-muted mt-0.5">
                      {item.symbol && <span className="mr-1">{item.symbol}</span>}
                      {timeAgo(item.triggered_at)}
                    </p>
                  </div>
                  <span className={cn(
                    'text-[10px] px-1.5 py-0.5 rounded shrink-0',
                    item.severity === 'CRITICAL' ? 'bg-loss/10 text-loss' :
                    item.severity === 'WARNING'  ? 'bg-accent-amber/10 text-accent-amber' :
                    'bg-accent-blue/10 text-accent-blue'
                  )}>
                    {item.severity}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Right column: scope breakdown + most triggered */}
        <div className="flex flex-col gap-4">
          {/* Scope breakdown */}
          <div className="bg-surface-2 border border-border-subtle rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border-subtle">
              <span className="text-xs font-medium text-text-primary uppercase tracking-wide">By Scope</span>
            </div>
            <div className="px-4 py-3 space-y-2">
              {statsLoading ? (
                <div className="text-xs text-text-muted">Loading…</div>
              ) : stats && Object.entries(stats.by_scope).map(([scope, count]) => (
                <div key={scope} className="flex items-center justify-between">
                  <span className={cn('text-[10px] px-2 py-0.5 rounded font-medium', SCOPE_COLORS[scope] ?? 'bg-surface-3 text-text-muted')}>
                    {scope}
                  </span>
                  <span className="text-xs text-text-secondary tabular-nums">{count}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Most triggered */}
          <div className="bg-surface-2 border border-border-subtle rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border-subtle">
              <span className="text-xs font-medium text-text-primary uppercase tracking-wide">Most Triggered</span>
            </div>
            <div className="divide-y divide-border-subtle">
              {statsLoading ? (
                <div className="px-4 py-6 text-center text-xs text-text-muted">Loading…</div>
              ) : (stats?.most_triggered ?? []).length === 0 ? (
                <div className="px-4 py-6 text-center text-xs text-text-muted">None yet</div>
              ) : stats!.most_triggered.map(item => (
                <div key={item.alert_id} className="px-4 py-2 flex items-center justify-between">
                  <span className="text-xs text-text-primary truncate">{item.name}</span>
                  <span className="text-xs text-accent-amber tabular-nums shrink-0 ml-2">×{item.count}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
