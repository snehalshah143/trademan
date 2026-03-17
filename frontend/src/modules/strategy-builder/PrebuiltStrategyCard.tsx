import { MiniPayoffSVG } from '@/components/charts/MiniPayoffSVG'
import type { MiniPayoffStrategy } from '@/components/charts/MiniPayoffSVG'

interface PrebuiltStrategyCardProps {
  name: string
  strategy: MiniPayoffStrategy
  onClick: () => void
  disabled?: boolean
}

export function PrebuiltStrategyCard({ name, strategy, onClick, disabled }: PrebuiltStrategyCardProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="flex flex-col items-center gap-1.5 p-2.5 rounded-lg border border-border-subtle bg-[#0f1d2e] hover:border-accent-blue hover:scale-[1.02] transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:border-border-subtle disabled:hover:scale-100"
    >
      <MiniPayoffSVG strategy={strategy} />
      <span className="text-[11px] text-text-secondary font-medium">{name}</span>
    </button>
  )
}
