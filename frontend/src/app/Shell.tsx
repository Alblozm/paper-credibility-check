import { useQueries } from '@tanstack/react-query'
import { Link, Outlet, useNavigate, useRouterState } from '@tanstack/react-router'
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { startedAt } from '../constants/credibility'
import { Assistant } from '../features/assistant/Assistant'
import { useLiveProgress } from '../features/check/useLiveProgress'
import { Icon } from '../ui/Icon'
import { ProviderMark } from '../ui/ProviderMark'
import { isMock } from './api'
import { closeTab, useTabs } from './jobs'
import { usePairedQuality } from './pairing'
import { checkQuery, isRunning, useCheck, useModels, useScoring } from './queries'

const titleOf = (value: ReturnType<typeof useCheck>['data']) => value?.paper?.title
  ?? (value?.source.type === 'arxiv' ? `arXiv:${value.source.arxiv_id}` : value ? 'Uploaded PDF' : 'Opening…')

/**
 * One tab per opened check. A running check shows a progress pie and percentage, so it stays visible
 * and reachable from any page (spec 3.7). The waiting panel shrinks into its tab.
 */
const clock = (iso: string) => new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })

/** Each tab shows the model it ran with; tabs for the same paper also show their start time, so runs stay apart. */
function CheckTab({ id, on, sameTitle, neighbour }: { id: string; on: boolean; sameTitle: boolean; neighbour: string | null }) {
  const navigate = useNavigate()
  const { data, isError } = useCheck(id)
  const models = useModels()
  const running = isRunning(data)
  const live = useLiveProgress(running ? data : undefined)
  const percent = Math.round(live?.percent ?? 0)
  const title = isError && !data ? 'Not found' : titleOf(data)
  const model = models.data?.models.find(item => item.model_id === data?.model_id) ?? models.data?.pending.find(item => item.model_id === data?.model_id)
  const hint = data ? `${title}\n${model?.display_name ?? data.model_id} · started ${startedAt(data.created_at)}` : title
  // Like a browser: closing the tab being read opens its neighbour (right first, then left), or the start page.
  const close = () => {
    closeTab(id)
    if (on) void (neighbour ? navigate({ to: '/check/$id', params: { id: neighbour } }) : navigate({ to: '/' }))
  }
  return <span className={`cs-doctab ${on ? 'is-on' : ''} ${running ? 'is-running' : ''}`} title={hint}
    style={{ viewTransitionName: running && !on ? `check-${id}` : undefined } as CSSProperties}>
    <Link to="/check/$id" params={{ id }} className="cs-doctab-link">
      {running
        ? <span className="cs-tabpie" style={{ '--p': percent } as CSSProperties} role="img" aria-label={`${percent}% complete`} />
        : model ? <ProviderMark provider={model.provider} /> : <Icon name="document_pdf" />}
      <span className="cs-doctab-title">{title}</span>
      {sameTitle && data && <span className="cs-doctab-time">{clock(data.created_at)}</span>}
      {running && <span className="cs-doctab-pct">{percent}%</span>}
    </Link>
    <button type="button" className="cs-ibtn is-sm" title="Close tab" aria-label={`Close ${title}`} onClick={close}><Icon name="dismiss" /></button>
  </span>
}

function ReportActions({ id }: { id: string }) {
  const { data } = useCheck(id)
  // Both parts of the run go into the raw export.
  const qualityId = usePairedQuality(data ? { assessment_id: data.assessment_id, created_at: data.created_at, title: data.paper?.title } : undefined)
  const quality = useScoring(qualityId).data
  const [copied, setCopied] = useState(false)
  const [menu, setMenu] = useState(false)
  const box = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!menu) return
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !box.current?.contains(event.target as Node)) setMenu(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close) }
  }, [menu])
  if (!data || data.status !== 'completed') return null
  const copyLink = async () => {
    try { await navigator.clipboard.writeText(window.location.href); setCopied(true); window.setTimeout(() => setCopied(false), 1800) } catch { /* clipboard blocked */ }
  }
  const exportJson = () => {
    const bundle = { run: data.assessment_id, credibility: data, quality: quality ?? null }
    const url = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' }))
    const link = Object.assign(document.createElement('a'), { href: url, download: `${data.assessment_id}.json` })
    link.click()
    URL.revokeObjectURL(url)
    setMenu(false)
  }
  return <>
    <button type="button" className="cs-tbtn" onClick={() => void copyLink()}><Icon name="link" /><span className="max-sm:hidden">{copied ? 'Link copied' : 'Copy link'}</span></button>
    <span ref={box} className="relative">
      <button type="button" className="cs-tbtn is-primary" aria-expanded={menu} onClick={() => setMenu(value => !value)}><Icon name="arrow_download" /><span className="max-sm:hidden">Export</span><Icon name="chevron_down" /></button>
      {menu && <span className="cs-menu cs-glass is-down" role="menu">
        <a role="menuitem" href={`/check/${id}/print`} target="_blank" rel="noreferrer" onClick={() => setMenu(false)}><Icon name="document_pdf" /><span>Report (PDF)</span><small>Both assessments and run details, via the print dialog</small></a>
        <button type="button" role="menuitem" onClick={exportJson}><Icon name="code" /><span>Raw data (JSON)</span><small>Both assessments of this run, as recorded</small></button>
      </span>}
    </span>
  </>
}

export function Shell() {
  const pathname = useRouterState({ select: state => state.location.pathname })
  const match = pathname.match(/^\/check\/([^/]+)(\/print)?/)
  const checkId = match?.[1] ?? null
  const tabs = useTabs()
  const shown = checkId && !tabs.includes(checkId) ? [...tabs, checkId] : tabs
  // Titles that appear on more than one tab: those tabs add their start time.
  const tabQueries = useQueries({ queries: shown.map(id => checkQuery(id)) })
  const tabData = new Map(shown.map((id, index) => [id, tabQueries[index]?.data]))
  const counts = new Map<string, number>()
  for (const value of tabData.values()) counts.set(titleOf(value), (counts.get(titleOf(value)) ?? 0) + 1)
  const repeated = new Set([...counts].filter(([, count]) => count > 1).map(([title]) => title))
  if (match?.[2]) return <div className="cs-root"><Outlet /></div>
  return <div className="cs-root">
    <header className="cs-titlebar">
      <Link to="/" className="cs-brand" aria-label="PaperTrace home" title="PaperTrace · Follow the evidence.">
        <span className="cs-brand-lockup"><img src="/brand/papertrace-logo.png" width="2160" height="728" alt="PaperTrace" /></span>
      </Link>
      <nav className="cs-doctabs" aria-label="Open assessments">
        <Link to="/" className={`cs-doctab is-plain ${pathname === '/' ? 'is-on' : ''}`} aria-label="New assessment"><Icon name="add" /><span className={shown.length ? 'max-md:hidden' : ''}>New assessment</span></Link>
        {shown.map((id, index) => <CheckTab key={id} id={id} on={id === checkId} sameTitle={repeated.has(titleOf(tabData.get(id)))}
          neighbour={shown[index + 1] ?? shown[index - 1] ?? null} />)}
      </nav>
      <span className="flex-1" />
      {isMock && <span className="cs-pill gap max-sm:hidden">Sample data</span>}
      <Link to="/reports" className="cs-tbtn" aria-current={pathname === '/reports' ? 'page' : undefined}><Icon name="history" /><span className="max-sm:hidden">History</span></Link>
      {checkId && <ReportActions id={checkId} />}
    </header>
    <Outlet />
    <Assistant />
  </div>
}
