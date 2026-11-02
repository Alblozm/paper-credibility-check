import { useRef, type CSSProperties } from 'react'
import { useAnimatedNumber, useViewCount } from './motion'

const START = 225
const SWEEP = 270
const angle = (fraction: number) => START + SWEEP * fraction
const clamp = (value: number) => Math.max(0, Math.min(1, value))
/** Colour on the doubtful-to-trustworthy scale at a fraction of the arc. */
const scoreColour = (fraction: number) =>
  `color-mix(in oklch, var(--color-score-high) ${Math.round(clamp(fraction) * 100)}%, var(--color-score-low))`

interface Props {
  /** score: 0–10 trial score. progress: 0–100 percent of the whole check. */
  mode: 'score' | 'progress'
  value: number | null
  /** Category in score mode, current stage in progress mode. */
  label: string
  sub?: string
  size?: number
  small?: boolean
  /** Change to replay the count-up. */
  replayKey?: unknown
  /** Shared-element name for page transitions. */
  transitionName?: string
  /** Band edges drawn as gaps on the arc. */
  bands?: number[]
}

/** Overall score: thin open arc with a knocked-out indicator (design spec 3.1). */
export function ScoreGauge({ mode, value, label, sub, size = 212, small = false, replayKey, transitionName, bands = [3, 6, 8] }: Props) {
  const scale = mode === 'score' ? 10 : 100
  const target = value ?? 0
  // The score counts up again every time the gauge comes back into view.
  const root = useRef<HTMLDivElement>(null)
  const entries = useViewCount(root)
  const shown = useAnimatedNumber(mode === 'score' && entries === 0 ? 0 : target, mode === 'score' ? 1500 : 400, mode === 'score' ? `${replayKey}-${entries}` : 'progress')
  // The category appears once the count-up lands, so it never flashes through lower bands.
  const settled = mode === 'score' && Math.abs(shown - target) < 0.005
  const fraction = clamp(shown / scale)
  const text = value == null ? '—' : mode === 'score' ? shown.toFixed(1) : `${Math.round(shown)}%`
  const style = { '--gauge-size': `${size}px`, viewTransitionName: transitionName } as CSSProperties
  return <div ref={root} className={`cs-gauge ${mode === 'progress' ? 'is-busy' : ''} ${small ? 'is-small' : ''}`} style={style}
    role="img" aria-label={value == null ? `${label}. No score.` : mode === 'score' ? `Trial score ${target.toFixed(1)} out of 10. ${label}.` : `${Math.round(target)}% complete. ${label}.`}>
    <div className="cs-gauge-dial">
      <div className="cs-gauge-ring" />
      {bands.map(edge => <i key={edge} className="cs-gauge-rot cs-gauge-sep" style={{ '--a': `${angle(edge / 10)}deg` } as CSSProperties} />)}
      <i className="cs-gauge-rot cs-gauge-cap" style={{ '--a': `${angle(0)}deg`, '--c': 'var(--color-score-low)' } as CSSProperties} />
      <i className="cs-gauge-rot cs-gauge-cap" style={{ '--a': `${angle(1)}deg`, '--c': 'var(--color-score-high)' } as CSSProperties} />
      {value != null && <div className="cs-gauge-rot cs-gauge-ind" style={{ '--a': `${angle(fraction)}deg`, '--c': mode === 'score' ? scoreColour(fraction) : 'var(--color-accent)' } as CSSProperties}><i /></div>}
      <div className="cs-gauge-mid" aria-hidden="true"><b className="cs-num">{text}</b><span>{mode === 'score' ? '/ 10' : 'in progress'}</span></div>
      <span className="cs-gauge-end is-l" aria-hidden="true">0</span><span className="cs-gauge-end is-r" aria-hidden="true">{mode === 'score' ? 10 : 100}</span>
    </div>
    <div className="cs-gauge-cat" aria-hidden="true">
      <h4 style={{ opacity: mode === 'progress' || settled || value == null ? 1 : 0 }}>{label}</h4>
      {sub && <p>{sub}</p>}
    </div>
  </div>
}
