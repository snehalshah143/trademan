import { useState } from 'react'
import { Bell, BarChart2, List, Layers } from 'lucide-react'
import { cn } from '@/lib/utils'
import { AlertDashboard } from '@/components/AlertManager/AlertDashboard'
import { AlertsByPosition } from '@/components/AlertManager/AlertsByPosition'
import { AlertHistory } from '@/components/AlertManager/AlertHistory'
import { AlertTemplates } from '@/components/AlertManager/AlertTemplates'

type Tab = 'overview' | 'by-position' | 'history' | 'templates'

const TABS: Array<{ id: Tab; label: string; icon: React.ElementType }> = [
  { id: 'overview',     label: 'Overview',     icon: BarChart2 },
  { id: 'by-position',  label: 'By Position',  icon: Bell },
  { id: 'history',      label: 'History',      icon: List },
  { id: 'templates',    label: 'Templates',    icon: Layers },
]

export function AlertManager() {
  const [tab, setTab] = useState<Tab>('overview')

  return (
    <div className="flex h-full">
      {/* Left sidebar */}
      <aside className="w-48 shrink-0 border-r border-border-subtle bg-surface-1 flex flex-col">
        <div className="px-4 py-4 border-b border-border-subtle">
          <h2 className="text-sm font-bold text-text-primary uppercase tracking-wide">Alert Manager</h2>
        </div>
        <nav className="flex-1 py-2">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={cn(
                'w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-left transition-colors border-l-2',
                tab === id
                  ? 'text-text-primary bg-surface-3 border-accent-blue'
                  : 'text-text-secondary hover:text-text-primary hover:bg-surface-2 border-transparent'
              )}
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
        </nav>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto custom-scroll bg-surface-0">
        {tab === 'overview'    && <AlertDashboard />}
        {tab === 'by-position' && <AlertsByPosition />}
        {tab === 'history'     && <AlertHistory />}
        {tab === 'templates'   && <AlertTemplates />}
      </main>
    </div>
  )
}
