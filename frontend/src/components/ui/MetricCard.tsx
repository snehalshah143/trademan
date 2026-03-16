import { cn } from '@/lib/utils'

interface MetricCardProps {
  label: string
  value: string | number
  trend?: 'up' | 'down' | null
  valueClass?: string
  className?: string
  compact?: boolean
}

export function MetricCard({ label, value, trend, valueClass, className, compact }: MetricCardProps) {
  return (
    <div className={cn('bg-surface-2 border border-border-subtle rounded-md', compact ? 'px-3 py-2' : 'px-4 py-3', className)}>
      <div className="text-text-muted text-xs uppercase tracking-wider font-medium mb-1">{label}</div>
      <div className={cn('num font-semibold', compact ? 'text-num-base' : 'text-num-lg', valueClass)}>
        {trend === 'up' && <span className="text-profit mr-1">▲</span>}
        {trend === 'down' && <span className="text-loss mr-1">▼</span>}
        {value}
      </div>
    </div>
  )
}
