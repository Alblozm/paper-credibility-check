import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { reducedMotion } from './motion'

/**
 * Which section of an outline is being read, in the window or in a scrolling container. A clicked entry stays lit
 * until the reader scrolls by hand (wheel, touch or keys); at the end of the content the last section is lit, since
 * a short last section never reaches the top.
 */
export function useSectionSpy(ids: string[], container?: RefObject<HTMLElement | null>) {
  const key = ids.join('|')
  const list = useMemo(() => key.split('|').filter(Boolean), [key])
  const [on, setOn] = useState(list[0])
  const pinned = useRef<string | null>(null)

  useEffect(() => {
    const box = container?.current ?? null
    const sync = () => {
      if (pinned.current) return
      const top = (box ? box.getBoundingClientRect().top : 0) + 120
      let current = list[0]
      for (const id of list) {
        const node = document.getElementById(id)
        if (node && node.getBoundingClientRect().top <= top) current = id
      }
      const atEnd = box ? box.scrollTop + box.clientHeight >= box.scrollHeight - 4
        : window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4
      setOn(atEnd ? list.at(-1)! : current)
    }
    const release = () => { if (pinned.current) { pinned.current = null; sync() } }
    const scroller: HTMLElement | Window = box ?? window
    sync()
    scroller.addEventListener('scroll', sync, { passive: true })
    for (const type of ['wheel', 'touchmove', 'keydown'] as const) window.addEventListener(type, release, { passive: true })
    return () => {
      scroller.removeEventListener('scroll', sync)
      for (const type of ['wheel', 'touchmove', 'keydown'] as const) window.removeEventListener(type, release)
    }
  }, [list, container])

  const go = (id: string) => {
    pinned.current = id
    setOn(id)
    document.getElementById(id)?.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' })
  }
  return { on, go }
}
