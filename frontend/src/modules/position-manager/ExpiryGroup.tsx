import { LegRow } from './LegRow'
import type { LiveLeg } from '@hooks/useLivePositions'

interface ExpiryGroupProps {
  expiry:    string
  legs:      LiveLeg[]
  onExitLeg: (legId: string) => void
}

export function ExpiryGroup({ legs, onExitLeg }: ExpiryGroupProps) {
  return (
    <>
      {legs.map((leg) => (
        <LegRow key={leg.id} leg={leg} onExit={onExitLeg} />
      ))}
    </>
  )
}
