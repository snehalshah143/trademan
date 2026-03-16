import { cn } from '@/lib/utils'

type BadgeVariant =
  | 'buy' | 'sell'
  | 'profit' | 'loss'
  | 'active' | 'draft' | 'exited' | 'error' | 'pending'
  | 'default'

interface BadgeProps {
  variant?: BadgeVariant
  children: React.ReactNode
  className?: string
}

const variantClasses: Record<BadgeVariant, string> = {
  buy:     'bg-blue-500/15 text-blue-400 border border-blue-500/25',
  sell:    'bg-red-500/15 text-red-400 border border-red-500/25',
  profit:  'bg-green-500/15 text-profit border border-green-500/20',
  loss:    'bg-red-500/15 text-loss border border-red-500/20',
  active:  'bg-green-500/15 text-profit border border-green-500/20',
  draft:   'bg-surface-3 text-text-secondary border border-border-default',
  exited:  'bg-surface-3 text-text-muted border border-border-subtle',
  error:   'bg-red-900/30 text-red-400 border border-red-500/30',
  pending: 'bg-amber-500/15 text-accent-amber border border-amber-500/25',
  default: 'bg-surface-3 text-text-secondary border border-border-default',
}

export function Badge({ variant = 'default', children, className }: BadgeProps) {
  return (
    <span className={cn('badge', variantClasses[variant], className)}>
      {children}
    </span>
  )
}
