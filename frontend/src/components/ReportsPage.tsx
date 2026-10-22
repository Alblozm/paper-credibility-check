import { useEffect, useRef, useState } from 'react'
import type { AssessmentApi } from '../api/assessmentApi'
import { categoryNames } from '../constants/credibility'
import { labels } from '../constants/labels'
import type { Assessment, ReviewSummary } from '../types/assessment'
import type { CredibilityApi, CredibilityAssessment, CredibilityHistoryItem } from '../types/credibility'

interface Props {
  scoringApi: AssessmentApi
  credibilityApi: CredibilityApi
  onOpenScoring: (assessment: Assessment) => void
  onOpenCredibility: (assessment: CredibilityAssessment) => void
  onNew: () => void
}

type Kind = 'all' | 'credibility' | 'scoring'
type Row =
  | { kind: 'credibility'; id: string; date: string; item: CredibilityHistoryItem }
  | { kind: 'scoring'; id: string; date: string; item: ReviewSummary }

function reportDate(value: string) {
  return new Date(value).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' })
}

/** Both kinds of saved report in one list, newest first. Opening one never re-runs it. */
export function ReportsPage({ scoringApi, credibilityApi, onOpenScoring, onOpenCredibility, onNew }: Props) {
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState<Kind>('all')
  const [rows, setRows] = useState<Row[] | null>(null)
  const [totals, setTotals] = useState({ credibility: 0, scoring: 0 })
  const [error, setError] = useState<string | null>(null)
  const [opening, setOpening] = useState<string | null>(null)
  const opener = useRef<AbortController | null>(null)
  useEffect(() => () => opener.current?.abort(), [])

  useEffect(() => {
    let active = true
    const empty = { items: [], total: 0, offset: 0, limit: 20 }
    Promise.allSettled([
      credibilityApi.listAssessments ? credibilityApi.listAssessments(search, 0) : Promise.resolve(empty),
      scoringApi.listAssessments(search, 'date', 0),
    ]).then(([credibility, scoring]) => {
      if (!active) return
      const next: Row[] = []
      if (credibility.status === 'fulfilled') next.push(...credibility.value.items.map(item => ({ kind: 'credibility' as const, id: item.assessment_id, date: item.completed_at ?? item.created_at, item })))
      if (scoring.status === 'fulfilled') next.push(...scoring.value.items.map(item => ({ kind: 'scoring' as const, id: item.assessment_id, date: item.completed_at ?? item.created_at, item })))
      setRows(next.sort((a, b) => Date.parse(b.date) - Date.parse(a.date)))
      setTotals({
        credibility: credibility.status === 'fulfilled' ? credibility.value.total : 0,
        scoring: scoring.status === 'fulfilled' ? scoring.value.total : 0,
      })
      const failed = [credibility, scoring].find(result => result.status === 'rejected')
      setError(failed?.status === 'rejected' ? (failed.reason instanceof Error ? failed.reason.message : 'Some saved reports could not be loaded.') : null)
    })
    return () => { active = false }
  }, [credibilityApi, scoringApi, search])

  async function open(row: Row) {
    opener.current?.abort()
    const controller = new AbortController()
    opener.current = controller
    setOpening(row.id); setError(null)
    try {
      if (row.kind === 'credibility') {
        const value = await credibilityApi.getAssessment(row.id, controller.signal)
        if (!controller.signal.aborted) onOpenCredibility(value)
      } else {
        const value = await scoringApi.getAssessment(row.id, controller.signal)
        if (!controller.signal.aborted) onOpenScoring(value)
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'This report could not be opened.')
    } finally {
      if (opener.current === controller) { opener.current = null; setOpening(null) }
    }
  }

  const shown = (rows ?? []).filter(row => kind === 'all' || row.kind === kind)
  const loaded = { credibility: rows?.filter(row => row.kind === 'credibility').length ?? 0, scoring: rows?.filter(row => row.kind === 'scoring').length ?? 0 }
  const truncated = loaded.credibility < totals.credibility || loaded.scoring < totals.scoring

  return <section className="view history" aria-labelledby="reports-title">
    <div className="history-head">
      <div>
        <p className="eyebrow">Saved on this server</p>
        <h1 id="reports-title">Reports</h1>
        <p>Credibility checks and paper scores in one place. Opening a report does not run the analysis again.</p>
      </div>
      <button className="button button-primary" type="button" onClick={onNew}>Check a paper</button>
    </div>

    <form className="history-search" onSubmit={event => { event.preventDefault(); setRows(null); setSearch(searchInput.trim()) }}>
      <label className="visually-hidden" htmlFor="report-search">Search saved reports</label>
      <input id="report-search" value={searchInput} onChange={event => setSearchInput(event.target.value)} placeholder="Search by title or arXiv ID" />
      <button className="button button-primary" type="submit">Search</button>
    </form>

    <div className="history-sort" role="group" aria-label="Report type">
      <span>Show</span>
      {([['all', 'All'], ['credibility', 'Credibility checks'], ['scoring', 'Paper scores']] as const).map(([value, label]) => (
        <button key={value} type="button" className={`history-sort-button ${kind === value ? 'is-active' : ''}`} aria-pressed={kind === value} onClick={() => setKind(value)}>{label}</button>
      ))}
    </div>

    {error && <div className="notice notice-amber history-error" role="alert"><span>{error}</span></div>}

    {rows === null ? <div className="history-empty" role="status">Loading saved reports…</div>
      : shown.length ? <div className="history-list">
        {shown.map(row => <article className="history-card" key={`${row.kind}-${row.id}`}>
          <div className="history-card-copy">
            <h2>{row.kind === 'credibility' ? row.item.title || row.item.arxiv_id || 'Untitled paper' : row.item.title}</h2>
            <p>{row.kind === 'credibility'
              ? row.item.title ? row.item.arxiv_id ?? 'Uploaded PDF' : row.item.error_code ? `Stopped with ${row.item.error_code}` : 'Title not recovered'
              : row.item.authors.join(', ') || 'Authors unavailable'}</p>
            <div className="history-card-tags">
              <span className={`chip ${row.kind === 'credibility' ? 'chip-violet' : 'chip-outline'}`}>{row.kind === 'credibility' ? 'Credibility check' : 'Paper scoring'}</span>
              {row.kind === 'scoring' && row.item.is_demo && <span className="chip chip-amber">Sample output</span>}
              {row.item.status === 'failed'
                ? <span className="chip chip-amber">Could not complete</span>
                : row.kind === 'credibility'
                  ? <span className="chip chip-accent">{row.item.overall_score === null ? 'Unscored' : `${row.item.overall_score.toFixed(1)} / 10`} · {row.item.category ? categoryNames[row.item.category] ?? row.item.category : 'no category'}</span>
                  : <span className="chip chip-accent">{row.item.overall_score == null ? 'Unscored' : `${row.item.overall_score.toFixed(1)} / 10`}{row.item.rating && ` · ${labels.rating[row.item.rating as keyof typeof labels.rating] ?? row.item.rating}`}</span>}
            </div>
          </div>
          <div className="history-card-actions">
            <button className="button button-secondary" type="button" disabled={opening === row.id} onClick={() => void open(row)}>{opening === row.id ? 'Opening…' : 'Open report'}</button>
            <span>{reportDate(row.date)}</span>
          </div>
        </article>)}
      </div>
      : <div className="history-empty">
        <h2>{search ? 'No matching reports' : 'No saved reports yet'}</h2>
        <p>{search ? 'Try another title or arXiv ID.' : 'Finished checks and paper scores appear here.'}</p>
      </div>}
    {rows !== null && truncated && <p className="history-pagination">Showing the latest {loaded.credibility + loaded.scoring} of {totals.credibility + totals.scoring}. Search to find older reports.</p>}
  </section>
}
