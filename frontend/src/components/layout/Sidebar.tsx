import { NavLink } from 'react-router-dom'
import { BarChart2, Layers, List, Settings } from 'lucide-react'
import { useLTPStore } from '@store/ltpStore'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { to: '/',        label: 'Positions',        Icon: BarChart2 },
  { to: '/builder', label: 'Strategy Builder',  Icon: Layers },
  { to: '/orders',  label: 'Order Book',        Icon: List },
  { to: '/settings',label: 'Settings',          Icon: Settings },
]

export function Sidebar() {
  const connectionStatus = useLTPStore((s) => s.connectionStatus)

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
