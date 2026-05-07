import { NavLink } from 'react-router-dom'
import { BarChart2, Layers, List, Settings, Bell, Eye } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useLTPStore } from '@store/ltpStore'
import { alertRuleService } from '@/services/alertService'
import { monitorService } from '@/services/monitorService'
import { monitorAlertService } from '@/services/monitorService'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { to: '/',         label: 'Position Manager', Icon: BarChart2 },
  { to: '/monitors', label: 'Monitored',         Icon: Eye },
  { to: '/builder',  label: 'Strategy Builder',  Icon: Layers },
  { to: '/alerts',   label: 'Alerts',            Icon: Bell },
  { to: '/orders',   label: 'Order Book',        Icon: List },
  { to: '/settings', label: 'Settings',          Icon: Settings },
]

export function Sidebar() {
  const connectionStatus = useLTPStore((s) => s.connectionStatus)

  const { data: alertStats } = useQuery({
    queryKey: ['alert-stats'],
    queryFn: () => alertRuleService.getStats(),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const { data: monitorAlertStats } = useQuery({
    queryKey: ['monitor-alert-stats'],
    queryFn: () => monitorAlertService.getStats(),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const { data: monitors = [] } = useQuery({
    queryKey: ['monitored-positions', 'ACTIVE'],
    queryFn: () => monitorService.listAll({ status: 'ACTIVE' }),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const firedToday = (alertStats?.fired_today ?? 0) + (monitorAlertStats?.fired_today ?? 0)
  const activeMonitors = monitors.length

  const dotClass =
    connectionStatus === 'CONNECTED'   ? 'bg-profit animate-pulse-dot' :
    connectionStatus === 'RECONNECTING'? 'bg-accent-amber' :
    'bg-loss'

  const dotLabel =
    connectionStatus === 'CONNECTED'    ? 'LIVE' :
    connectionStatus === 'RECONNECTING' ? 'RECONNECTING' :
    connectionStatus === 'CONNECTING'   ? 'CONNECTING' :
    'OFFLINE'

  return (
    <aside className="w-60 shrink-0 bg-surface-1 border-r border-border-subtle flex flex-col h-full">
      {/* Logo */}
      <div className="h-12 flex items-center px-4 border-b border-border-subtle">
        <span className="text-text-primary font-bold tracking-widest uppercase text-sm">
          TRADE<span className="text-accent-blue">MAN</span>
        </span>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-2">
        {NAV_ITEMS.map(({ to, label, Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }: { isActive: boolean }) =>
              cn(
                'flex items-center gap-3 px-4 py-2.5 text-sm transition-colors relative',
                isActive
                  ? 'text-text-primary bg-surface-3 border-l-2 border-accent-blue pl-[14px]'
                  : 'text-text-secondary hover:text-text-primary hover:bg-surface-2 border-l-2 border-transparent pl-[14px]'
              )
            }
          >
            <Icon size={15} />
            {label}
            {to === '/alerts' && firedToday > 0 && (
              <span className="ml-auto flex items-center justify-center w-4 h-4 rounded-full bg-loss text-white text-[9px] font-bold leading-none">
                {firedToday > 9 ? '9+' : firedToday}
              </span>
            )}
            {to === '/monitors' && activeMonitors > 0 && (
              <span className="ml-auto flex items-center justify-center w-4 h-4 rounded-full bg-accent-blue text-white text-[9px] font-bold leading-none">
                {activeMonitors > 9 ? '9+' : activeMonitors}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Connection status */}
      <div className="px-4 py-3 border-t border-border-subtle flex items-center gap-2">
        <div className={cn('w-2 h-2 rounded-full', dotClass)} />
        <span className="text-xs text-text-muted">{dotLabel}</span>
      </div>
    </aside>
  )
}
