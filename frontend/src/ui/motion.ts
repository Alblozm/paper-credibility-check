import { useEffect, useRef, useState, type RefObject } from 'react'
import { flushSync } from 'react-dom'

export const reducedMotion = () => typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)

const easeOut = (t: number) => 1 - Math.pow(1 - t, 3)

/**
 * Animates towards `target`. A new `restartKey` starts again from `from`; otherwise the value
 * glides from wherever it is now, so live progress never jumps. Reduced motion shows the target at once.
 */
export function useAnimatedNumber(target: number, duration: number, restartKey?: unknown, from = 0) {
  const reduce = reducedMotion()
  const [value, setValue] = useState(from)
  const current = useRef(from)
  const seenKey = useRef(restartKey)
  useEffect(() => {
    if (reduce) return
    let start = current.current
    if (seenKey.current !== restartKey) { seenKey.current = restartKey; start = from }
    let frame = 0
    const t0 = performance.now()
    const step = (now: number) => {
      // A frame's timestamp can precede t0 slightly; clamping keeps the first frame from showing "-0.0".
      const t = duration > 0 ? Math.max(0, Math.min(1, (now - t0) / duration)) : 1
      current.current = start + (target - start) * easeOut(t)
      setValue(current.current)
      if (t < 1) frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [target, duration, restartKey, from, reduce])
  return reduce ? target : value
}

/**
 * How many times the element has come into view. Scores replay their count-up on every new entry:
 * scrolling back, expanding a folded widget or returning to the tab (spec 2.8).
 */
export function useViewCount(ref: RefObject<Element | null>, threshold = 0.4) {
  const [count, setCount] = useState(() => (typeof IntersectionObserver === 'undefined' ? 1 : 0))
  useEffect(() => {
    const node = ref.current
    if (!node || typeof IntersectionObserver === 'undefined') return
    let inView = false
    const observer = new IntersectionObserver(entries => {
      const visible = entries.some(entry => entry.isIntersecting)
      if (visible && !inView) setCount(value => value + 1)
      inView = visible
    }, { threshold })
    observer.observe(node)
    // Returning to the browser tab counts as a new appearance when the score is on screen.
    const onShow = () => { if (document.visibilityState === 'visible' && inView) setCount(value => value + 1) }
    document.addEventListener('visibilitychange', onShow)
    return () => { observer.disconnect(); document.removeEventListener('visibilitychange', onShow) }
  }, [ref, threshold])
  return count
}

/** Current time, ticking every second while `active`. */
export function useNow(active = true) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])
  return now
}

/** Runs a state change inside a view transition where the browser supports it (spec 4.3). */
export function transition(update: () => void) {
  const start = (document as Document & { startViewTransition?: (cb: () => void) => unknown }).startViewTransition
  if (!start || reducedMotion() || document.visibilityState !== 'visible') { update(); return }
  start.call(document, () => flushSync(update))
}
