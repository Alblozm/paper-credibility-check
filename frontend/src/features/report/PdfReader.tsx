import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type ReactNode, type Ref } from 'react'
import type { PDFPageProxy, RenderTask } from 'pdfjs-dist'
import { reducedMotion } from '../../ui/motion'
import type { Mark, SkillId } from './model'

type Pdfjs = typeof import('pdfjs-dist')
let pdfjsPromise: Promise<Pdfjs> | null = null
/** pdf.js is large, so it loads only when a report opens. */
function loadPdfjs() {
  pdfjsPromise ??= Promise.all([import('pdfjs-dist'), import('pdfjs-dist/build/pdf.worker.min.mjs?url')]).then(([lib, worker]) => {
    lib.GlobalWorkerOptions.workerSrc = worker.default
    return lib
  })
  return pdfjsPromise
}

export type PdfSource = { kind: 'url'; url: string } | { kind: 'file'; file: File }

interface Rect { x: number; y: number; w: number; h: number }
interface TextItem { str: string; width: number; transform: number[] }
interface PageRecord {
  n: number
  page: PDFPageProxy
  width: number
  height: number
  key: number
  task: RenderTask | null
  rendered: boolean
  index?: { text: string; map: [number, number][]; items: TextItem[] }
}

export interface PdfReaderHandle {
  goTo: (markId: string) => Promise<void>
  goPage: (page: number) => void
  /** Finds the next match after the last one; returns the page or null. */
  find: (query: string) => Promise<number | null>
}

interface Props {
  source: PdfSource
  marks: Map<string, Mark>
  layers: Record<SkillId, boolean>
  selected: string | null
  reviewed: ReadonlySet<string>
  zoom: number
  onSelect: (markId: string) => void
  onPageChange: (page: number) => void
  onLoaded: (pages: PDFPageProxy[]) => void
  card: (mark: Mark) => ReactNode
  handle: Ref<PdfReaderHandle>
}

const normalise = (value: string) => value.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]/g, '')

/** Finds an excerpt in a page's text layer, tolerating hyphenation and line breaks. */
function locate(index: PageRecord['index'], excerpt: string): [number, number] | null {
  const needle = normalise(excerpt)
  if (!index || !needle) return null
  let start = index.text.indexOf(needle)
  if (start >= 0) return [start, start + needle.length - 1]
  const head = needle.slice(0, 50), tail = needle.slice(-50)
  start = index.text.indexOf(head)
  if (start < 0) return null
  const end = index.text.indexOf(tail, start)
  return [start, end >= 0 && end - start < needle.length * 1.5 ? end + tail.length - 1 : start + head.length - 1]
}

export function PdfReader({ source, marks, layers, selected, reviewed, zoom, onSelect, onPageChange, onLoaded, card, handle }: Props) {
  const scroller = useRef<HTMLDivElement>(null)
  const pdfjs = useRef<Pdfjs | null>(null)
  const records = useRef<PageRecord[]>([])
  const shells = useRef(new Map<number, HTMLDivElement>())
  const rectCache = useRef(new Map<string, Rect[]>())
  const layoutKey = useRef(0)
  const current = useRef(1)
  const lastFind = useRef({ query: '', page: 0, position: -1 })
  const [pages, setPages] = useState<{ n: number; width: number; height: number }[]>([])
  const [error, setError] = useState<string | null>(null)
  const [width, setWidth] = useState(760)
  const [hover, setHover] = useState<{ id: string; left: number; top: number; above: boolean } | null>(null)
  const hideTimer = useRef(0)
  const state = useRef({ marks, layers, selected, reviewed })
  useLayoutEffect(() => { state.current = { marks, layers, selected, reviewed } })

  // Load the document.
  useEffect(() => {
    let cancelled = false
    let destroy: (() => void) | null = null
    setError(null); setPages([]); records.current = []
    ;(async () => {
      try {
        const lib = await loadPdfjs()
        pdfjs.current = lib
        const task = lib.getDocument(source.kind === 'url' ? { url: source.url } : { data: new Uint8Array(await source.file.arrayBuffer()) })
        destroy = () => { void task.destroy() }
        const doc = await task.promise
        const all = await Promise.all(Array.from({ length: doc.numPages }, (_, i) => doc.getPage(i + 1)))
        if (cancelled) return
        records.current = all.map((page, i) => {
          const view = page.getViewport({ scale: 1 })
          return { n: i + 1, page, width: view.width, height: view.height, key: -1, task: null, rendered: false }
        })
        setPages(records.current.map(({ n, width: w, height: h }) => ({ n, width: w, height: h })))
        onLoaded(all)
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : 'The PDF could not be loaded.')
      }
    })()
    return () => { cancelled = true; destroy?.() }
    // onLoaded is a stable callback from the parent; reloading on its identity would refetch the PDF.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source])

  // Page width follows the reading column and the zoom level.
  useEffect(() => {
    const node = scroller.current
    if (!node) return
    const observer = new ResizeObserver(() => setWidth(Math.max(240, Math.min(node.clientWidth - 64, 860))))
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  const pageWidth = width * zoom

  const indexText = useCallback(async (record: PageRecord) => {
    if (record.index) return record.index
    const content = await record.page.getTextContent()
    const items = content.items.filter((item): item is TextItem & typeof item => 'str' in item) as unknown as TextItem[]
    let text = ''
    const map: [number, number][] = []
    items.forEach((item, i) => {
      for (let j = 0; j < item.str.length; j++) for (const ch of normalise(item.str[j])) { text += ch; map.push([i, j]) }
    })
    record.index = { text, map, items }
    return record.index
  }, [])

  const rectsFor = useCallback((record: PageRecord, range: [number, number], scale: number): Rect[] => {
    const lib = pdfjs.current, index = record.index
    if (!lib || !index) return []
    const viewport = record.page.getViewport({ scale })
    const [ia, ja] = index.map[range[0]], [ib, jb] = index.map[range[1]]
    const out: Rect[] = []
    for (let i = ia; i <= ib; i++) {
      const item = index.items[i]
      if (!item.str) continue
      const tx = lib.Util.transform(viewport.transform, item.transform) as number[]
      const fontHeight = Math.hypot(tx[2], tx[3]), w = item.width * viewport.scale, length = item.str.length
      const s = i === ia ? ja : 0, e = i === ib ? jb + 1 : length
      const segment = w * (e - s) / length
      if (segment > 0.5) out.push({ x: tx[4] + w * s / length, y: tx[5] - fontHeight * .86, w: segment, h: fontHeight * 1.08 })
    }
    out.sort((a, b) => a.y - b.y || a.x - b.x)
    const merged: Rect[] = []
    out.forEach(rect => {
      const last = merged.at(-1)
      if (last && Math.abs(last.y - rect.y) < 2 && rect.x - (last.x + last.w) < 6) { last.w = Math.max(last.x + last.w, rect.x + rect.w) - last.x; last.h = Math.max(last.h, rect.h) }
      else merged.push({ ...rect })
    })
    return merged
  }, [])

  const drawMarks = useCallback(async (record: PageRecord) => {
    await indexText(record)
    const overlay = shells.current.get(record.n)?.querySelector<HTMLDivElement>('.cs-ov')
    if (!overlay) return
    const { marks: all, layers: on, selected: chosen, reviewed: seen } = state.current
    const scale = pageWidth / record.width
    overlay.replaceChildren()
    for (const mark of all.values()) {
      if (mark.page !== record.n || !on[mark.skill]) continue
      const range = locate(record.index, mark.evidence.excerpt)
      const rects = range ? rectsFor(record, range, scale) : []
      rectCache.current.set(mark.id, rects)
      const extra = `${seen.has(mark.id) ? ' is-reviewed' : ''}${chosen === mark.id ? ' is-sel' : ''}`
      rects.forEach(rect => {
        const node = document.createElement('div')
        node.className = `cs-hl ${mark.kind}${extra}`
        node.dataset.m = mark.id
        Object.assign(node.style, { left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.w}px`, height: `${rect.h}px` })
        overlay.appendChild(node)
      })
      if (rects.length && mark.skill === 'claims') {
        const top = rects[0].y, bottom = rects.at(-1)!.y + rects.at(-1)!.h
        const bar = document.createElement('div')
        bar.className = `cs-mk${seen.has(mark.id) ? ' is-reviewed' : ''}`
        bar.dataset.m = mark.id
        Object.assign(bar.style, { top: `${top}px`, height: `${Math.max(10, bottom - top)}px` })
        overlay.appendChild(bar)
      }
    }
  }, [indexText, rectsFor, pageWidth])

  const renderPage = useCallback(async (record: PageRecord | undefined) => {
    const shell = record && shells.current.get(record.n)
    if (!record || !shell || record.key === layoutKey.current) return
    record.key = layoutKey.current
    const canvas = shell.querySelector('canvas')!
    const ratio = Math.min(window.devicePixelRatio || 1, 2)
    const viewport = record.page.getViewport({ scale: pageWidth / record.width * ratio })
    record.task?.cancel()
    canvas.width = Math.floor(viewport.width); canvas.height = Math.floor(viewport.height)
    record.task = record.page.render({ canvas, viewport })
    try { await record.task.promise } catch { return }
    shell.classList.remove('is-loading')
    record.rendered = true
    await drawMarks(record)
  }, [drawMarks, pageWidth])

  // Lazy rendering: pages within 700px of the viewport render; a layout change re-renders them.
  useEffect(() => {
    const root = scroller.current
    if (!root || !pages.length) return
    layoutKey.current++
    records.current.forEach(record => { record.rendered = false })
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) void renderPage(records.current[Number((entry.target as HTMLElement).dataset.n) - 1])
    }), { root, rootMargin: '700px 0px' })
    shells.current.forEach(node => observer.observe(node))
    return () => observer.disconnect()
  }, [pages, renderPage])

  // Layers and review state change the drawing, not the page.
  useEffect(() => { records.current.filter(record => record.rendered).forEach(record => void drawMarks(record)) }, [layers, reviewed, marks, drawMarks])
  useEffect(() => {
    const root = scroller.current
    if (!root) return
    root.querySelectorAll('.cs-hl.is-sel').forEach(node => node.classList.remove('is-sel'))
    if (selected) root.querySelectorAll(`.cs-hl[data-m="${CSS.escape(selected)}"]`).forEach(node => node.classList.add('is-sel'))
  }, [selected])

  const scrollTo = (top: number, smooth = true) => scroller.current?.scrollTo({ top, behavior: smooth && !reducedMotion() ? 'smooth' : 'auto' })

  useImperativeHandle(handle, () => ({
    async goTo(markId) {
      const mark = state.current.marks.get(markId)
      const record = mark && records.current[mark.page - 1]
      const shell = record && shells.current.get(record.n)
      if (!mark || !record || !shell) return
      scrollTo(shell.offsetTop - 16, false)
      if (!record.rendered) await renderPage(record)
      const rect = rectCache.current.get(markId)?.[0]
      scrollTo(shell.offsetTop + (rect ? rect.y : 0) - 140)
    },
    goPage(page) {
      const shell = shells.current.get(Math.max(1, Math.min(records.current.length, page)))
      if (shell) scrollTo(shell.offsetTop - 16)
    },
    async find(query) {
      const needle = normalise(query)
      if (needle.length < 2 || !records.current.length) return null
      const again = lastFind.current.query === needle
      const start = again ? lastFind.current.page : current.current - 1
      for (let k = 0; k <= records.current.length; k++) {
        const record = records.current[(start + k) % records.current.length]
        const index = await indexText(record)
        const position = index.text.indexOf(needle, again && k === 0 ? lastFind.current.position + 1 : 0)
        if (position < 0) continue
        lastFind.current = { query: needle, page: record.n - 1, position }
        const shell = shells.current.get(record.n)!
        scrollTo(shell.offsetTop - 16, false)
        if (!record.rendered) await renderPage(record)
        const rects = rectsFor(record, [position, position + needle.length - 1], pageWidth / record.width)
        const overlay = shell.querySelector('.cs-ov')!
        rects.forEach(rect => {
          const node = document.createElement('div')
          node.className = 'cs-hl is-find'
          Object.assign(node.style, { left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.w}px`, height: `${rect.h}px` })
          overlay.appendChild(node)
          window.setTimeout(() => node.remove(), 2600)
        })
        if (rects[0]) scrollTo(shell.offsetTop + rects[0].y - 160)
        return record.n
      }
      lastFind.current = { query: '', page: 0, position: -1 }
      return null
    },
  }), [indexText, rectsFor, renderPage, pageWidth])

  const onScroll = () => {
    const root = scroller.current
    if (!root) return
    const middle = root.scrollTop + root.clientHeight * .3
    let page = 1
    shells.current.forEach((node, n) => { if (node.offsetTop <= middle && n > page) page = n })
    if (page !== current.current) { current.current = page; onPageChange(page) }
  }

  const markAt = (target: EventTarget | null) => {
    const node = (target as HTMLElement | null)?.closest?.<HTMLElement>('[data-m]')
    return node ? { node, mark: state.current.marks.get(node.dataset.m!) } : null
  }
  const showCard = (markId: string, node: HTMLElement) => {
    const root = scroller.current!
    window.clearTimeout(hideTimer.current)
    const a = node.getBoundingClientRect(), box = root.getBoundingClientRect()
    const left = Math.max(8, Math.min(a.left - box.left + root.scrollLeft, root.clientWidth - 346 + root.scrollLeft))
    // The card opens above the highlight when there is no room below it.
    const above = a.bottom + 230 > box.bottom
    setHover({ id: markId, left, above, top: above ? a.top - box.top + root.scrollTop - 8 : a.bottom - box.top + root.scrollTop + 8 })
  }
  const hideCard = () => { window.clearTimeout(hideTimer.current); hideTimer.current = window.setTimeout(() => setHover(null), 240) }

  const hovered = hover ? marks.get(hover.id) : null
  return <div ref={scroller} className="cs-doc" onScroll={onScroll}
    onMouseOver={event => {
      const hit = markAt(event.target)
      if (!hit?.mark || (event.target as HTMLElement).closest('.cs-pop')) return
      scroller.current?.querySelectorAll(`.cs-hl[data-m="${CSS.escape(hit.mark.id)}"]`).forEach(node => node.classList.add('is-hover'))
      showCard(hit.mark.id, hit.node)
    }}
    onMouseOut={event => {
      const hit = markAt(event.target)
      if (!hit?.mark || (event.target as HTMLElement).closest('.cs-pop')) return
      scroller.current?.querySelectorAll('.cs-hl.is-hover').forEach(node => node.classList.remove('is-hover'))
      hideCard()
    }}
    onClick={event => {
      if ((event.target as HTMLElement).closest('.cs-pop')) return
      const hit = markAt(event.target)
      if (hit?.mark) { onSelect(hit.mark.id); showCard(hit.mark.id, hit.node) }
    }}>
    {error ? <div className="mx-auto mt-16 max-w-[460px] text-center text-muted">
      <p className="cs-serif text-title text-ink">The PDF could not be loaded.</p>
      <p className="mt-2">{error}. The assessment widgets remain available.</p>
    </div> : <div className="cs-pages">
      {!pages.length && <p className="py-20 text-muted">Loading the paper…</p>}
      {pages.map(page => <div key={page.n} data-n={page.n} className="cs-pg is-loading"
        ref={node => { if (node) shells.current.set(page.n, node); else shells.current.delete(page.n) }}
        style={{ width: pageWidth, height: pageWidth * page.height / page.width }}>
        <span className="cs-pno">p. {page.n}</span><canvas /><div className="cs-ov" />
      </div>)}
    </div>}
    {hover && hovered && <div className="cs-pop cs-glass"
      style={{ left: hover.left, top: hover.top, transform: hover.above ? 'translateY(-100%)' : undefined }}
      onMouseEnter={() => window.clearTimeout(hideTimer.current)} onMouseLeave={() => setHover(null)}>
      {card(hovered)}
    </div>}
  </div>
}
