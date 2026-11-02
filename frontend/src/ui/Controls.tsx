import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { Icon, type IconName } from './Icon'

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (next: boolean) => void; label: string }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} className="cs-toggle" onClick={() => onChange(!checked)} />
}

interface SegmentedProps<T extends string> {
  value: T
  options: { value: T; label: ReactNode; icon?: IconName; title?: string }[]
  onChange: (next: T) => void
  label: string
  disabled?: boolean
  /** Large: the switches that change what a whole page shows. */
  size?: 'md' | 'lg'
}

/** A choice of two to four, with a thumb that slides to the selected option (measured, so options can differ in width). */
export function Segmented<T extends string>({ value, options, onChange, label, disabled, size = 'md' }: SegmentedProps<T>) {
  const track = useRef<HTMLSpanElement>(null)
  const [thumb, setThumb] = useState<{ x: number; w: number } | null>(null)
  useLayoutEffect(() => {
    const node = track.current
    if (!node) return
    const measure = () => {
      const on = node.querySelector<HTMLElement>('[aria-checked="true"]')
      setThumb(on ? { x: on.offsetLeft, w: on.offsetWidth } : null)
    }
    measure()
    // Option labels can change width (a live percentage), which moves the thumb too.
    const observer = new ResizeObserver(measure)
    node.querySelectorAll('button').forEach(button => observer.observe(button))
    return () => observer.disconnect()
  }, [value, options.length])
  return <span ref={track} className={`cs-seg ${size === 'lg' ? 'is-lg' : ''}`} role="radiogroup" aria-label={label}>
    {thumb && <span className="cs-seg-thumb" style={{ transform: `translateX(${thumb.x}px)`, width: thumb.w }} aria-hidden="true" />}
    {options.map(option => <button key={option.value} type="button" role="radio" aria-checked={option.value === value}
      title={option.title} disabled={disabled} onClick={() => onChange(option.value)}>
      {option.icon && <Icon name={option.icon} />}{option.label}
    </button>)}
  </span>
}
