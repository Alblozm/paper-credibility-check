import { useEffect, useState } from 'react'

/** One line that rotates through what is happening now, sliding up like a departure board (spec 3.9). */
export function Ticker({ lines, every = 3200 }: { lines: string[]; every?: number }) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (lines.length < 2) return
    const timer = window.setInterval(() => setTick(value => value + 1), every)
    return () => window.clearInterval(timer)
  }, [lines.length, every])
  const line = lines.length ? lines[tick % lines.length] : ''
  return <span className="cs-ticker" aria-hidden="true"><span key={line} className="cs-ticker-line">{line}</span></span>
}
