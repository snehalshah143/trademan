import { LegRow } from './LegRow'
import type { LiveLeg } from '@hooks/useLivePositions'

interface ExpiryGroupProps {
  expiry:       string
  legs:         LiveLeg[]
  legNumberMap: Map<string, number>
  onExitLeg:    (legId: string) => void
}

export function ExpiryGroup({ legs, legNumberMap, onExitLeg }: ExpiryGroupProps) {
  return (
    <>
      {legs.map((leg) => (
        <LegRow
          key={leg.id}
          leg={leg}
          legNumber={legNumberMap.get(leg.id) ?? 1}
          onExit={onExitLeg}
        />
      ))}
    </>
  )
}
