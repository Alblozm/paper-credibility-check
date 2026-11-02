import { useSearch } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Group, Panel, Separator, usePanelRef } from 'react-resizable-panels'
import type { PDFPageProxy } from 'pdfjs-dist'
import { savePdf, useLocalPdf } from '../../app/pdfStore'
import { useModels } from '../../app/queries'
import { duration, startedAt } from '../../constants/credibility'
import { IconButton } from '../../ui/Button'
import { Icon, type IconName } from '../../ui/Icon'
import { reasonName } from '../../ui/names'
import { Badge, OutcomePill, ReferencePill } from '../../ui/Pill'
import { Widget } from '../../ui/Widget'
import { LeftPane } from './LeftPane'
import { buildModel, markCount, skills, type Mark, type SkillId } from './model'
import { PdfReader, type PdfReaderHandle, type PdfSource } from './PdfReader'
import { ReportReader } from './ReportReader'
import { ClaimsBody, CodeBody, EvidenceBody, NotesBody, ProcessBody, ReferencesBody, VerdictBody, type Reported } from './widgets'

// The quality assessment is a peer tab of the run (RunParts), not a widget here.
type WidgetId = 'verdict' | 'claims' | 'refs' | 'code' | 'evidence' | 'process' | 'notes'
const widgetMeta: Record<WidgetId, { icon: IconName; title: string; closable?: boolean }> = {
  verdict: { icon: 'gauge', title: 'Verdict' },
  claims: { icon: 'text_quote', title: 'Claims' },
  refs: { icon: 'book_open', title: 'References' },
  code: { icon: 'code', title: 'Code' },
  evidence: { icon: 'highlight', title: 'Evidence' },
  process: { icon: 'timeline', title: 'Process record', closable: true },
  notes: { icon: 'note', title: 'Notes', closable: true },
}
const defaultDock = { order: ['verdict', 'claims', 'refs', 'code', 'evidence', 'process', 'notes'] as WidgetId[], hidden: ['notes'] as WidgetId[], collapsed: [] as WidgetId[] }
type DockState = typeof defaultDock

/** A saved layout may name widgets that no longer exist; drop those and append new ones. */
function cleanDock(dock: DockState): DockState {
  const known = (list: WidgetId[]) => list.filter(widget => widget in widgetMeta)
  const order = known(dock.order)
  return { order: [...order, ...defaultDock.order.filter(widget => !order.includes(widget))], hidden: known(dock.hidden), collapsed: known(dock.collapsed) }
}

function stored<T>(key: string, fallback: T): T {
  try { const value = localStorage.getItem(key); return value ? { ...fallback, ...JSON.parse(value) } : fallback } catch { return fallback }
}
function save(key: string, value: unknown) { try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage blocked */ } }

function useWidth(ref: React.RefObject<HTMLElement | null>) {
  // The window is a good first guess, so the layout does not switch after the first measure.
  const [width, setWidth] = useState(() => window.innerWidth)
  useEffect(() => {
    const node = ref.current
    if (!node) return
    const observer = new ResizeObserver(() => setWidth(node.clientWidth))
    observer.observe(node)
    return () => observer.disconnect()
  }, [ref])
  return width
}

/** Report page (spec 5.3): paper in the middle, checks as widgets on the right. */
export function ReportView({ assessment, toolSlot }: { assessment: Reported; toolSlot: HTMLElement | null }) {
  const { report } = assessment
  const id = assessment.assessment_id
  const model = useMemo(() => buildModel(report), [report])
  const [layers, setLayers] = useState<Record<SkillId, boolean>>({ claims: true, refs: true, code: true })
  const [selected, setSelected] = useState<string | null>(null)
  const [reviewed, setReviewed] = useState<Set<string>>(() => new Set(stored<{ ids: string[] }>(`papertrace-reviewed-${id}`, { ids: [] }).ids))
  const [zoom, setZoom] = useState(1)
  const [page, setPage] = useState(1)
  const [pageInput, setPageInput] = useState('1')
  const [pdfPages, setPdfPages] = useState<PDFPageProxy[]>([])
  const [dock, setDock] = useState<DockState>(() => cleanDock(stored('papertrace-dock', defaultDock)))
  const [message, setMessage] = useState('')
  // On a phone the outline is an overlay, so it starts closed.
  const [leftOpen, setLeftOpen] = useState(() => window.innerWidth > 700)
  const [dockOpen, setDockOpen] = useState(true)
  const [onWidget, setOnWidget] = useState<WidgetId>('verdict')
  const [grip, setGrip] = useState<WidgetId | null>(null)
  const [dragging, setDragging] = useState<WidgetId | null>(null)
  const [menu, setMenu] = useState(false)
  const reader = useRef<PdfReaderHandle>(null)
  const leftPanel = usePanelRef()
  const dockPanel = usePanelRef()
  const root = useRef<HTMLDivElement>(null)
  const dockPane = useRef<HTMLDivElement>(null)
  const width = useWidth(root)
  const narrow = width > 0 && width <= 700
  // Two ways to read the assessment, switched in the run bar: the paper with highlights and widgets, or the report as one document.
  const view = useSearch({ from: '/check/$id' }).view === 'report' ? 'report' : 'paper'
  const models = useModels()
  const modelName = models.data?.models.find(item => item.model_id === assessment.model_id)?.display_name ?? assessment.model_id

  // Keyed on plain values: a refetch returns new objects, and reloading the PDF would lose the place.
  const arxivId = assessment.paper?.arxiv_id ?? (assessment.source.type === 'arxiv' ? assessment.source.arxiv_id : null)
  // An upload is shown from this browser's copy; without one the paper falls back to excerpts.
  const local = useLocalPdf(arxivId ? null : id)
  const source = useMemo<PdfSource | null>(() => {
    if (arxivId) return { kind: 'url', url: `https://arxiv.org/pdf/${arxivId}` }
    return local.file ? { kind: 'file', file: local.file } : null
  }, [arxivId, local.file])

  useEffect(() => save('papertrace-dock', dock), [dock])
  useEffect(() => save(`papertrace-reviewed-${id}`, { ids: [...reviewed] }), [reviewed, id])
  useEffect(() => {
    if (!message) return
    const timer = window.setTimeout(() => setMessage(''), 2600)
    return () => window.clearTimeout(timer)
  }, [message])
  const evidenceList = useMemo(() => [...model.marks.values()]
    .filter(mark => layers[mark.skill] && (mark.skill === 'claims' || mark.kind === 'ref-gap'))
    .sort((a, b) => a.page - b.page), [model, layers])

  const select = useCallback((markId: string | undefined, scroll = true) => {
    if (!markId) return
    const mark = model.marks.get(markId)
    if (mark && !layers[mark.skill]) setLayers(value => ({ ...value, [mark.skill]: true }))
    setSelected(markId)
    if (scroll) void reader.current?.goTo(markId)
  }, [model, layers])

  // The rail keeps the widget the reader picked lit until they scroll the dock themselves.
  const pinned = useRef<WidgetId | null>(null)
  const openWidget = (widget: WidgetId) => {
    setOnWidget(widget)
    pinned.current = widget
    if (!dockOpen) { dockPanel.current?.expand(); setDockOpen(true) }
    setDock(value => ({ ...value, hidden: value.hidden.filter(item => item !== widget), collapsed: value.collapsed.filter(item => item !== widget) }))
    window.requestAnimationFrame(() => {
      const node = dockPane.current?.querySelector<HTMLElement>(`[data-widget="${widget}"]`)
      if (node && dockPane.current) dockPane.current.scrollTo({ top: node.offsetTop - 10, behavior: 'smooth' })
    })
  }
  const copyQuote = async (mark: Mark) => {
    try { await navigator.clipboard.writeText(`“${mark.evidence.excerpt}” (p. ${mark.page})`); setMessage('Quotation copied') }
    catch { setMessage('Copying was blocked by the browser') }
  }
  const toggleReviewed = (markId: string) => {
    setReviewed(value => { const next = new Set(value); if (next.has(markId)) next.delete(markId); else next.add(markId); return next })
  }
  const setLayer = (skill: SkillId, on: boolean) => setLayers(value => ({ ...value, [skill]: on }))
  const toggleLeft = () => {
    if (narrow) { setLeftOpen(value => !value); return }
    if (leftPanel.current?.isCollapsed()) leftPanel.current.expand(); else leftPanel.current?.collapse()
  }
  const toggleDock = () => { if (dockPanel.current?.isCollapsed()) dockPanel.current.expand(); else dockPanel.current?.collapse() }
  const setZoomStep = (direction: number) => {
    const steps = [0.7, 0.85, 1, 1.15, 1.3, 1.5]
    setZoom(value => steps[Math.max(0, Math.min(steps.length - 1, steps.indexOf(value) + direction))])
  }

  const selectedClaims = new Set(selected ? model.marks.get(selected)?.claims.map(claim => claim.claim_id) ?? [] : [])
  const card = (mark: Mark) => {
    const isClaim = mark.skill === 'claims'
    const claim = mark.claims[0], ref = mark.refs[0]
    const section = mark.evidence.paper_locator?.section_title
    const reason = ref ? report.reference_verification_summary.unresolved_items.find(item => item.reference_id === ref.reference_id)?.reason_code : null
    return <>
      <div className="is-who">
        <span><Icon name={isClaim ? 'text_quote' : 'book_open'} />{isClaim ? `${claim.label} · ${claim.importance} claim · ${mark.kind === 'support' ? 'supporting passage' : 'claim as stated'}` : 'Reference'}</span>
        {isClaim ? <OutcomePill outcome={claim.outcome} /> : <ReferencePill outcome={ref.outcome} />}
      </div>
      <p className="is-st">{isClaim ? claim.statement : ref.parsed.title || ref.raw_text.slice(0, 120)}</p>
      <p className="is-meta">{isClaim ? `p. ${mark.page}${section ? ` · ${section}` : ''}${mark.claims.length > 1 ? ` · also ${mark.claims.slice(1).map(item => item.label).join(', ')}` : ''}`
        : `${(ref.parsed.authors ?? []).slice(0, 2).join(', ')}${ref.parsed.year ? ` · ${ref.parsed.year}` : ''}${reason ? ` · ${reasonName(reason)}` : ''}`}</p>
      <div className="is-acts">
        <button type="button" onClick={() => void copyQuote(mark)}><Icon name="copy" />Copy quotation</button>
        <button type="button" onClick={() => { select(mark.id, false); openWidget(isClaim ? 'claims' : 'refs') }}><Icon name="open" />Show in {isClaim ? 'Claims' : 'References'}</button>
        <button type="button" className={reviewed.has(mark.id) ? 'is-on' : ''} onClick={() => toggleReviewed(mark.id)}><Icon name="checkmark" />{reviewed.has(mark.id) ? 'Reviewed' : 'Mark reviewed'}</button>
      </div>
    </>
  }

  const body = (widget: WidgetId): ReactNode => {
    switch (widget) {
      case 'verdict': return <VerdictBody report={report} model={model} />
      case 'claims': return <ClaimsBody model={model} selectedClaims={selectedClaims}
        onClaim={claimId => { const claim = model.claims.find(item => item.claim_id === claimId)!; select(claim.comparison_evidence_ids[0] ?? claim.claim_evidence_ids[0])}}
        onPage={(claimId, target) => { const claim = model.claims.find(item => item.claim_id === claimId)!; select([...claim.comparison_evidence_ids, ...claim.claim_evidence_ids].find(item => model.evidence.get(item)?.paper_locator?.page_start === target)) }} />
      case 'refs': return <ReferencesBody report={report} model={model} onReference={referenceId => { select(model.references.get(referenceId)?.paper_evidence_ids[0]) }} />
      case 'code': return <CodeBody report={report} model={model} />
      case 'evidence': return <EvidenceBody list={evidenceList} selected={selected} reviewed={reviewed} onSelect={markId => select(markId)} onCopy={mark => void copyQuote(mark)} onReview={toggleReviewed} />
      case 'process': return <ProcessBody report={report} />
      case 'notes': return <NotesBody id={id} />
    }
  }
  const badges: Partial<Record<WidgetId, [number, boolean]>> = { claims: [model.claims.length, false], refs: [report.reference_verification_summary.unverified_count, true] }
  const visible = dock.order.filter(widget => !dock.hidden.includes(widget))

  const onDragOver = (event: DragEvent) => {
    if (!dragging) return
    event.preventDefault()
    const nodes = [...(dockPane.current?.querySelectorAll<HTMLElement>('[data-widget]') ?? [])].filter(node => node.dataset.widget !== dragging)
    const after = nodes.find(node => event.clientY < node.getBoundingClientRect().top + node.offsetHeight / 2)?.dataset.widget as WidgetId | undefined
    setDock(value => {
      const order = value.order.filter(item => item !== dragging)
      const at = after ? order.indexOf(after) : order.length
      order.splice(at, 0, dragging)
      return order.join() === value.order.join() ? value : { ...value, order }
    })
  }
  const syncRail = () => {
    const pane = dockPane.current
    if (!pane) return
    if (pinned.current) return
    // A widget near the end may never reach the top of the dock, so at the end the last one is lit.
    const nodes = [...pane.querySelectorAll<HTMLElement>('[data-widget]')]
    const atEnd = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 4
    let on: WidgetId = visible[0]
    nodes.forEach(node => { if (node.offsetTop <= pane.scrollTop + 40) on = node.dataset.widget as WidgetId })
    if (atEnd && nodes.length) on = nodes.at(-1)!.dataset.widget as WidgetId
    setOnWidget(on)
  }

  const release = () => { if (pinned.current) { pinned.current = null; syncRail() } }

  const dockContent = <div className="cs-dock" onDragOver={onDragOver}>
    {visible.map(widget => {
      const meta = widgetMeta[widget]
      return <Widget key={widget} id={widget} icon={meta.icon} title={meta.title}
        collapsed={dock.collapsed.includes(widget)}
        onToggle={() => setDock(value => ({ ...value, collapsed: value.collapsed.includes(widget) ? value.collapsed.filter(item => item !== widget) : [...value.collapsed, widget] }))}
        onClose={meta.closable ? () => setDock(value => ({ ...value, hidden: [...value.hidden, widget] })) : undefined}
        onGrip={() => setGrip(widget)} draggable={grip === widget}
        onDragStart={event => { setDragging(widget); event.dataTransfer.effectAllowed = 'move' }}
        onDragEnd={() => { setDragging(null); setGrip(null) }}
        className={dragging === widget ? 'is-dragging' : ''}>
        {body(widget)}
      </Widget>
    })}
    <div className="cs-addw">
      {menu && <div className="cs-menu cs-glass" role="menu">
        {dock.hidden.length ? dock.hidden.map(widget => <button key={widget} type="button" role="menuitem" onClick={() => { setMenu(false); openWidget(widget) }}>
          <Icon name={widgetMeta[widget].icon} /><span>{widgetMeta[widget].title}</span>
        </button>) : <p className="cs-lead p-2">All widgets are already displayed.</p>}
      </div>}
      <button type="button" aria-expanded={menu} onClick={() => setMenu(value => !value)}><Icon name="add" />Add widget</button>
    </div>
  </div>

  // The reader's tools sit in the run bar beside the part and view switches (one bar, not two).
  const tools = <>
    <IconButton icon="panel_left" label="Show or hide the sidebar" pressed={narrow ? leftOpen : leftOpen} onClick={toggleLeft} />
    <span className="cs-sep" />
    <span className="cs-pager">
      <IconButton icon="chevron_left" label="Previous page" onClick={() => reader.current?.goPage(page - 1)} />
      <input value={pageInput} inputMode="numeric" aria-label="Page" onChange={event => setPageInput(event.target.value)}
        onKeyDown={event => { if (event.key === 'Enter') reader.current?.goPage(parseInt(pageInput, 10) || 1) }} onBlur={() => setPageInput(String(page))} />
      <span>/ {pdfPages.length || '–'}</span>
      <IconButton icon="chevron_right" label="Next page" onClick={() => reader.current?.goPage(page + 1)} />
    </span>
    {!narrow && <span className="cs-zoom"><IconButton icon="zoom_out" label="Zoom out" onClick={() => setZoomStep(-1)} /><span>{Math.round(zoom * 100)}%</span><IconButton icon="zoom_in" label="Zoom in" onClick={() => setZoomStep(1)} /></span>}
    {!narrow && <span className="cs-sep" />}
    <label className={`cs-find ${narrow ? 'flex-1' : ''}`}><Icon name="search" />
      <input placeholder="Search the paper" aria-label="Search the paper" autoComplete="off" disabled={!source}
        onKeyDown={async event => {
          if (event.key !== 'Enter') return
          const found = await reader.current?.find(event.currentTarget.value)
          setMessage(found ? `Match on page ${found}` : 'No match in the paper')
        }} />
    </label>
    <span className="flex-1" />
    <span className="flex gap-1">{skills.filter(skill => markCount(model, skill.id) > 0).map(skill => <button key={skill.id} type="button" className="cs-lay" aria-pressed={layers[skill.id]}
      title={`Show ${skill.name} highlights`} onClick={() => setLayer(skill.id, !layers[skill.id])}>
      <Icon name={skill.icon} />{width > 1140 && <span>{skill.short}</span>}<Badge>{markCount(model, skill.id)}</Badge>
    </button>)}</span>
    {!narrow && <><span className="cs-sep" /><IconButton icon="panel_right" label="Show or hide the widgets" pressed={dockOpen} onClick={toggleDock} /></>}
  </>
  const commandBar = view === 'paper' && toolSlot ? createPortal(tools, toolSlot) : null

  const centre = source
    ? <PdfReader source={source} marks={model.marks} layers={layers} selected={selected} reviewed={reviewed} zoom={zoom} handle={reader}
        onSelect={markId => select(markId, false)} onPageChange={next => { setPage(next); setPageInput(String(next)) }}
        onLoaded={setPdfPages} card={card} />
    : local.loading ? null : <Excerpts list={evidenceList} selected={selected} onSelect={markId => select(markId, false)}
        onFile={arxivId ? undefined : file => void savePdf(id, file)} />

  const longest = Math.max(...Object.values(report.execution.checks ?? {}).map(check => check?.elapsed_seconds ?? 0))
  const statusBar = <footer className="cs-statusbar">
    <span><Icon name="shield_checkmark" />{assessment.data_origin === 'live' ? 'Live assessment' : 'Sample data'} · {modelName}
      <span className="hidden sm:inline">· Started {startedAt(assessment.created_at)}</span>
      {longest > 0 && <><Icon name="clock" />Duration {duration(longest)}</>}
      <span className="hidden md:inline">· Rubric {report.rubric_version} (provisional) — trial score, not an approved assessment</span></span>
    <span><span className="is-msg" aria-live="polite">{message}</span>{source && view === 'paper' && <span>Page {page} of {pdfPages.length || '–'} · {Math.round(zoom * 100)}%</span>}</span>
  </footer>

  // minmax(0, 1fr): the rendered pages must not widen the column, or the page itself would scroll sideways.
  if (narrow) return <div ref={root} className="grid grid-cols-[minmax(0,1fr)]" style={{ viewTransitionName: 'doc-open' }}>
    {view === 'paper' ? <>
      {commandBar}
      <div className="relative h-[560px]">{centre}
        {leftOpen && <div className="absolute inset-y-0 left-0 z-20 w-[min(290px,85%)] shadow-[var(--shadow-3)]">
          <LeftPane model={model} pages={pdfPages} current={page} layers={layers} onLayer={setLayer} onPage={next => { reader.current?.goPage(next); setLeftOpen(false) }} />
        </div>}
      </div>
      <div className="cs-pane is-dock">{dockContent}</div>
    </> : <div className="h-[calc(100vh-132px)] min-h-[480px]"><ReportReader assessment={assessment} modelName={modelName} /></div>}
    {statusBar}
  </div>

  return <div ref={root} className="grid h-[calc(100vh-92px)] min-h-[620px] grid-cols-[minmax(0,1fr)] grid-rows-[minmax(0,1fr)_auto] overflow-x-clip" style={{ viewTransitionName: 'doc-open' }}>
    {commandBar}
    <div className="flex min-h-0">
      {view === 'report' ? <div className="min-w-0 flex-1"><ReportReader assessment={assessment} modelName={modelName} /></div> : <>
      <Group orientation="horizontal" className="min-w-0 flex-1">
        <Panel id="left" panelRef={leftPanel} defaultSize={width <= 920 ? '0px' : '252px'} minSize="190px" maxSize="380px" collapsible collapsedSize="0px"
          onResize={size => setLeftOpen(size.inPixels > 0)}>
          <LeftPane model={model} pages={pdfPages} current={page} layers={layers} onLayer={setLayer} onPage={next => reader.current?.goPage(next)} />
        </Panel>
        <Separator className="cs-sep-v" />
        <Panel id="doc" minSize="30%">{centre}</Panel>
        <Separator className="cs-sep-v" />
        <Panel id="dock" panelRef={dockPanel} defaultSize={width <= 1140 ? '312px' : '344px'} minSize="280px" maxSize="480px" collapsible collapsedSize="0px"
          onResize={size => setDockOpen(size.inPixels > 0)}>
          <div ref={dockPane} className="cs-pane is-dock" onScroll={syncRail} onWheel={release} onTouchMove={release} onKeyDown={release}>{dockContent}</div>
        </Panel>
      </Group>
      <nav className="cs-rail is-right w-11 shrink-0" aria-label="Widgets">
        {visible.map(widget => <button key={widget} type="button" className={`cs-rb ${onWidget === widget ? 'is-on' : ''}`} title={widgetMeta[widget].title}
          aria-label={widgetMeta[widget].title} onClick={() => openWidget(widget)}>
          <Icon name={widgetMeta[widget].icon} />{badges[widget] && <span className={`cs-badge ${badges[widget]![1] ? 'is-gap' : ''}`}>{badges[widget]![0]}</span>}
        </button>)}
        <span className="flex-1" />
        <button type="button" className="cs-rb" title="Add widget" aria-label="Add widget" onClick={() => { openWidget(visible.at(-1) ?? 'verdict'); setMenu(true) }}><Icon name="add" /></button>
      </nav>
      </>}
    </div>
    {statusBar}
  </div>
}

/** Without the PDF (an upload opened later), the passages are listed instead (spec 5.3). */
/** Evidence passages without the paper. For an upload this browser has no copy of, the reader can supply the file again. */
function Excerpts({ list, selected, onSelect, onFile }: { list: Mark[]; selected: string | null; onSelect: (markId: string) => void; onFile?: (file: File) => void }) {
  return <div className="cs-doc">
    <div className="mx-auto grid max-w-[760px] gap-3 px-6 py-8">
      {onFile ? <div className="cs-card flex flex-wrap items-center justify-between gap-3 p-3.5">
        <p className="cs-lead">This browser holds no copy of the uploaded PDF, so the evidence passages are listed with their page numbers. Select the same file to read them in place; it is kept in this browser.</p>
        <label className="cs-btn is-sm cursor-pointer"><Icon name="document_pdf" />Select the PDF
          <input type="file" accept="application/pdf,.pdf" className="sr-only" onChange={event => { const file = event.target.files?.[0]; if (file) onFile(file) }} /></label>
      </div> : <p className="cs-lead">The original PDF could not be shown; the evidence passages are listed with their page numbers.</p>}
      {list.map(mark => <button key={mark.id} type="button" onClick={() => onSelect(mark.id)}
        className={`cs-card grid gap-1.5 p-3.5 text-left ${selected === mark.id ? 'ring-2 ring-accent' : ''}`}>
        <span className="text-small text-muted">p. {mark.page}{mark.evidence.paper_locator?.section_title ? ` · ${mark.evidence.paper_locator.section_title}` : ''}</span>
        <span className={`cs-evq ${mark.kind === 'support' ? '' : 'is-claim'}`}>{mark.evidence.excerpt}</span>
      </button>)}
    </div>
  </div>
}
