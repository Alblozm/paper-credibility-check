import { useRef, type CSSProperties } from 'react'
import { useAnimatedNumber, useViewCount } from './motion'

interface Props {
  value: number | null
  /** Fallback score for an incomplete check: slate material with hatching. */
  gap?: boolean
}

function useScore(value: number | null) {
  const ref = useRef<HTMLDivElement>(null)
  const entries = useViewCount(ref)
  const shown = useAnimatedNumber(entries > 0 && value != null ? value : 0, 1300, entries)
  return { ref, shown, style: { '--f': shown / 10 } as CSSProperties }
}

const Number = ({ value, shown }: { value: number | null; shown: number }) =>
  <span className="cs-sv-n">{value == null ? '—' : shown.toFixed(1)}<small>/10</small></span>

const label = (name: string, value: number | null, gap?: boolean) =>
  `${name} trial score ${value == null ? 'not available' : `${value.toFixed(1)} out of 10`}${gap ? ', fallback score' : ''}`

/** Claims & evidence: volumetric knob with 21 ticks (spec 3.2). */
export function ScoreKnob({ value, gap }: Props) {
  const { ref, shown, style } = useScore(value)
  const ticks = Array.from({ length: 21 }, (_, i) => {
    const a = (135 + 270 * i / 20) * Math.PI / 180
    return <line key={i} className={shown > 0 && i / 20 <= shown / 10 + 1e-6 ? 'is-on' : ''}
      x1={31 + 26 * Math.cos(a)} y1={31 + 26 * Math.sin(a)} x2={31 + 30 * Math.cos(a)} y2={31 + 30 * Math.sin(a)} />
  })
  return <div ref={ref} className={`cs-sv ${gap ? 'is-gap' : ''}`} style={style} role="img" aria-label={label('Claims', value, gap)}>
    <div className="flex items-center gap-2.5">
      <div className="cs-dial" aria-hidden="true"><svg viewBox="0 0 62 62">{ticks}</svg><div className="cs-knob"><i className="cs-lit" /></div></div>
      <Number value={value} shown={shown} />
    </div>
  </div>
}

/** References: glass tube (spec 3.2). */
export function ScoreTube({ value, gap }: Props) {
  const { ref, shown, style } = useScore(value)
  return <div ref={ref} className={`cs-sv ${gap ? 'is-gap' : ''}`} style={style} role="img" aria-label={label('References', value, gap)}>
    <div className="cs-sv-row"><Number value={value} shown={shown} />
      <div className="cs-tube-wrap" aria-hidden="true"><div className="cs-tube cs-groove"><i className="cs-lit" /></div><div className="cs-scale"><span>0</span><span>5</span><span>10</span></div></div>
    </div>
  </div>
}

/** Code & reproduction: bead range (spec 3.2). */
export function ScoreBead({ value, gap }: Props) {
  const { ref, shown, style } = useScore(value)
  return <div ref={ref} className={`cs-sv ${gap ? 'is-gap' : ''}`} style={style} role="img" aria-label={label('Code', value, gap)}>
    <div className="cs-sv-row"><Number value={value} shown={shown} />
      <div className="cs-range-wrap" aria-hidden="true"><div className="cs-range cs-groove"><span /><i /></div><div className="cs-scale"><span>0</span><span>5</span><span>10</span></div></div>
    </div>
  </div>
}
