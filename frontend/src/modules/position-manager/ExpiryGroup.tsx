import { fmtExpiry, fmtINRCompact, profitLossClass, cn } from '@/lib/utils'
import { LegRow } from './LegRow'
import type { LiveLeg } from '@hooks/useLivePositions'

interface ExpiryGroupProps {
  expiry: string
  legs: LiveLeg[]
  checkedLegs: Set<string>
  onCheck: (legId: string, checked: boolean) => void
  onExitLeg: (legId: string) => void
}

export function ExpiryGroup({ expiry, legs, checkedLegs, onCheck, onExitLeg }: ExpiryGroupProps) {
  const subMTM = legs.reduce((s, l) => s + l.legMTM, 0)

  return (
    <>
      <tr className="bg-surface-2">
        <td colSpan={2} className="px-3 py-1.5 text-left">
          <span className="text-xs text-text-muted uppercase tracking-wider font-medium">
            {fmtExpiry(expiry) || expiry || 'No Expiry'}
          </span>
        </td>
        <td colSpan={5} />
        <td className="px-3 py-1.5 text-right">
          <span className={cn('num text-num-xs font-medium', profitLossClass(subMTM))}>
            {fmtINRCompact(subMTM)}
          </span>
        </td>
      </tr>
      {legs.map((leg) => (
        <LegRow
          key={leg.id}
          leg={leg}
          checked={checkedLegs.has(leg.id)}
          onCheck={onCheck}
          onExit={onExitLeg}
        />
      ))}
    </>
  )
}
