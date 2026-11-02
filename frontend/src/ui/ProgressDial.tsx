import { useId, type CSSProperties } from 'react'
import { useAnimatedNumber } from './motion'

// One SVG coordinate system, so every part scales together and nothing collides (spec 3.1b).
const C = { x: 120, y: 120 }
const R = 88
const SWEEP = 240
const toXY = (deg: number, r: number) => {
  const a = (deg * Math.PI) / 180
  return { x: C.x + r * Math.sin(a), y: C.y - r * Math.cos(a) }
}
const angleOf = (percent: number) => -SWEEP / 2 + (SWEEP * Math.max(0, Math.min(100, percent))) / 100
const start = toXY(-SWEEP / 2, R)
const end = toXY(SWEEP / 2, R)
const ARC = `M ${start.x.toFixed(2)} ${start.y.toFixed(2)} A ${R} ${R} 0 1 1 ${end.x.toFixed(2)} ${end.y.toFixed(2)}`
const ticks = Array.from({ length: 21 }, (_, i) => i * 5)

interface Props {
  /** 0–100 percent of the whole check. */
  percent: number
  /** Accessible description of the current stage. */
  label: string
  transitionName?: string
}

/** Speed-test style dial for check progress: the arc fills and the needle follows. */
export function ProgressDial({ percent, label, transitionName }: Props) {
  const shown = useAnimatedNumber(percent, 500, 'progress')
  const id = useId().replace(/:/g, '')
  const needle = angleOf(shown)
  const style = { viewTransitionName: transitionName, '--fill': `${(SWEEP * shown) / 100}deg` } as CSSProperties
  return <div className="cs-dial2" role="img" aria-label={`${Math.round(percent)}% complete. ${label}.`} style={style}>
    <svg viewBox="0 0 240 190" aria-hidden="true">
      <defs>
        <linearGradient id={`${id}-fill`} x1="0" x2="1" y1="0" y2="0">
          <stop offset="0" stopColor="var(--color-blue-200)" />
          <stop offset=".45" stopColor="var(--color-blue-400)" />
          <stop offset="1" stopColor="var(--color-blue-700)" />
        </linearGradient>
        <linearGradient id={`${id}-needle`} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor="var(--color-blue-500)" />
          <stop offset="1" stopColor="var(--color-ink)" />
        </linearGradient>
      </defs>
      {ticks.map(t => {
        const major = t % 25 === 0
        const a = toXY(angleOf(t), major ? 66 : 70), b = toXY(angleOf(t), 76)
        return <line key={t} className={`cs-dial2-tick ${major ? 'is-major' : ''} ${t <= shown ? 'is-passed' : ''}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
      })}
      {[0, 25, 50, 75, 100].map(t => {
        const p = toXY(angleOf(t), 106)
        return <text key={t} className="cs-dial2-scale" x={p.x} y={p.y + 3.5} textAnchor="middle">{t}</text>
      })}
      <path className="cs-dial2-track" d={ARC} />
      {shown > 0.5 && <path className="cs-dial2-fill" d={ARC} pathLength={100} strokeDasharray={`${shown} 100`} stroke={`url(#${id}-fill)`} />}
      <g transform={`rotate(${needle} ${C.x} ${C.y})`} className="cs-dial2-needle">
        <path d={`M ${C.x - 3.6} ${C.y} L ${C.x - 0.8} ${C.y - 72} L ${C.x + 0.8} ${C.y - 72} L ${C.x + 3.6} ${C.y} Z`} fill={`url(#${id}-needle)`} />
      </g>
      <circle className="cs-dial2-hub" cx={C.x} cy={C.y} r={9} />
      <circle cx={C.x} cy={C.y} r={3.4} fill="var(--color-accent)" />
      <text className="cs-dial2-value" x={C.x} y={174} textAnchor="middle">{Math.round(shown)}<tspan dx="1" className="cs-dial2-unit">%</tspan></text>
    </svg>
    {/* Light flowing along the filled arc. It is a CSS layer rather than SVG so the motion is a plain rotation the GPU can run. */}
    {shown > 0.5 && <span className="cs-dial2-sheen" aria-hidden="true"><span><i /></span></span>}
  </div>
}
