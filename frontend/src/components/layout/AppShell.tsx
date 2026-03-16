import { Sidebar } from './Sidebar'
import { Header } from './Header'
import { StaleBanner } from './StaleBanner'

interface AppShellProps {
  children: React.ReactNode
}

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="flex h-screen bg-surface-0 overflow-hidden">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <Header />
        <StaleBanner />
        <main className="flex-1 min-h-0 overflow-y-auto custom-scroll">
          {children}
        </main>
      </div>
    </div>
  )
}
