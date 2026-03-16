export type MiniPayoffStrategy =
  | 'straddle'
  | 'strangle'
  | 'bullcall'
  | 'bearput'
  | 'ironfly'
  | 'ironcondor'
  | 'coveredcall'
  | 'longcall'

interface MiniPayoffSVGProps {
  strategy: MiniPayoffStrategy
}

const SHAPES: Record<MiniPayoffStrategy, string> = {
  straddle:    '0,52 30,52 60,4 90,52 120,52',
  strangle:    '0,52 20,52 60,8 100,52 120,52',
  bullcall:    '0,40 40,40 80,16 120,16',
  bearput:     '0,16 40,16 80,40 120,40',
  ironfly:     '0,52 30,52 60,8 90,52 120,52',
  ironcondor:  '0,48 20,48 40,16 80,16 100,48 120,48',
  coveredcall: '0,44 60,20 80,16 120,16',
  longcall:    '0,40 50,40 80,10 120,4',
}

const MIDLINE = 28

function splitPoints(pointsStr: string): { profit: string; loss: string } {
  const pts = pointsStr.split(' ').map((p) => {
    const [x, y] = p.split(',').map(Number)
    return { x, y }
  })

  const profitPts: string[] = []
  const lossPts: string[] = []

  for (let i = 0; i < pts.length; i++) {
    const p = pts[i]
    if (i === 0) {
      if (p.y <= MIDLINE) profitPts.push(`${p.x},${p.y}`)
      else lossPts.push(`${p.x},${p.y}`)
      continue
    }

    const prev = pts[i - 1]
    // Check if line crosses midline
    if ((prev.y - MIDLINE) * (p.y - MIDLINE) < 0) {
      // Intersection point
      const t = (MIDLINE - prev.y) / (p.y - prev.y)
      const ix = prev.x + t * (p.x - prev.x)
      const cross = `${ix.toFixed(1)},${MIDLINE}`

      if (prev.y <= MIDLINE) {
        profitPts.push(cross)
        lossPts.push(cross)
        lossPts.push(`${p.x},${p.y}`)
      } else {
        lossPts.push(cross)
        profitPts.push(cross)
        profitPts.push(`${p.x},${p.y}`)
      }
    } else {
      if (p.y <= MIDLINE) profitPts.push(`${p.x},${p.y}`)
      else lossPts.push(`${p.x},${p.y}`)
    }
  }

  return { profit: profitPts.join(' '), loss: lossPts.join(' ') }
}

export function MiniPayoffSVG({ strategy }: MiniPayoffSVGProps) {
  const shape = SHAPES[strategy]
  const { profit, loss } = splitPoints(shape)

  return (
    <svg viewBox="0 0 120 56" width={120} height={56} xmlns="http://www.w3.org/2000/svg">
      {/* Zero line */}
      <line x1="0" y1={MIDLINE} x2="120" y2={MIDLINE} stroke="#3a3a4a" strokeWidth="0.5" />
      {/* Loss segments (red) */}
      {loss && (
        <polyline
          points={loss}
          fill="none"
          stroke="#ef4444"
          strokeWidth="1.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}
      {/* Profit segments (green) */}
      {profit && (
        <polyline
          points={profit}
          fill="none"
          stroke="#22c55e"
          strokeWidth="1.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}
    </svg>
  )
}
