import { useQueries, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useState, type CSSProperties, type MouseEvent } from 'react'
import { ApiError } from '../../api/httpAssessmentApi'
import { canDelete, deleteRun } from '../../app/deleteRun'
import { checkForScoring, useTabs } from '../../app/jobs'
import { pairAll } from '../../app/pairing'
import { checkQuery, isRunning, useCheckHistory, useModels, useScoringHistory } from '../../app/queries'
import { startQuality } from '../../app/quality'
import { categoryNames, startedAt } from '../../constants/credibility'
import { errorTitles, labels } from '../../constants/labels'
import type { ReviewSummary } from '../../types/assessment'
import type { CredibilityAssessment, CredibilityHistoryItem } from '../../types/credibility'
import { Button, IconButton } from '../../ui/Button'
import { Segmented } from '../../ui/Controls'
import { Icon } from '../../ui/Icon'
import { ProviderMark } from '../../ui/ProviderMark'
import { useLiveProgress } from '../check/useLiveProgress'

type Filter = 'all' | 'checks' | 'scores'

/**
 * One row per run (spec 5.4): the credibility assessment and the quality assessment started beside it, each with its
 * own state. A quality assessment run on its own is a row without a credibility part.
 */
type Row =
  | { kind: 'run'; id: string; date: string; check: CredibilityHistoryItem; quality: ReviewSummary | null; live?: CredibilityAssessment }
  | { kind: 'quality'; id: string; date: string; quality: ReviewSummary }

/** A run the history does not list (yet): still running, or newer than the loaded page. */
const asHistoryItem = (value: CredibilityAssessment): CredibilityHistoryItem => ({
  assessment_id: value.assessment_id, status: value.status === 'failed' ? 'failed' : 'completed', title: value.paper?.title ?? null,
  arxiv_id: value.paper?.arxiv_id ?? (value.source.type === 'arxiv' ? value.source.arxiv_id : null), source_type: value.source.type,
  model_id: value.model_id, overall_score: value.report?.overall.score ?? null, category: value.report?.overall.category ?? null,
  error_code: value.error?.code ?? null, created_at: value.created_at, completed_at: value.completed_at,
})

function Running({ assessment }: { assessment: CredibilityAssessment }) {
  const percent = Math.round(useLiveProgress(assessment)?.percent ?? 0)
  return <span className="inline-flex items-center gap-2 whitespace-nowrap text-small font-bold text-accent-strong">
    <span className="cs-tabpie" style={{ '--p': percent } as CSSProperties} aria-hidden="true" />Running · {percent}%</span>
}

const rating = (item: ReviewSummary) => labels.rating[item.rating as keyof typeof labels.rating] ?? item.rating ?? '—'

function Score({ value, label, onOpen }: { value: number | null; label: string; onOpen: (event: MouseEvent) => void }) {
  return <button type="button" className="cs-hpart" onClick={onOpen}>
    <span className="cs-num">{value == null ? '—' : value.toFixed(1)}</span><span className="is-label">{label}</span>
  </button>
}

export function ReportsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState<Filter>('all')
  const [input, setInput] = useState('')
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ id: string; text: string; error?: boolean } | null>(null)
  const [asking, setAsking] = useState<string | null>(null)
  useEffect(() => { const timer = window.setTimeout(() => setSearch(input.trim()), 250); return () => window.clearTimeout(timer) }, [input])
  const checks = useCheckHistory(search)
  const scores = useScoringHistory(search)
  const models = useModels()

  const listedChecks = checks.data?.items ?? []
  const scoreItems = scores.data?.items ?? []
  // The history lists finished checks only. Runs this browser knows about (open tabs, or the run a listed quality
  // assessment belongs to) are fetched one by one, so a running run gets its own row and its quality assessment
  // is not shown as a stand-alone row with no credibility part.
  const tabs = useTabs()
  const listed = new Set(listedChecks.map(item => item.assessment_id))
  const knownIds = [...new Set([...tabs, ...scoreItems.map(item => item.credibility_assessment_id ?? checkForScoring(item.assessment_id) ?? '')])].filter(id => id && !listed.has(id))
  const needle = search.toLowerCase()
  const known = useQueries({ queries: knownIds.map(id => checkQuery(id)) }).flatMap(query => (query.data ? [query.data] : []))
    .filter(value => !needle || `${value.paper?.title ?? ''} ${value.paper?.arxiv_id ?? ''}`.toLowerCase().includes(needle))
  const live = new Map(known.filter(value => isRunning(value)).map(value => [value.assessment_id, value]))
  const checkItems = [...listedChecks, ...known.map(asHistoryItem)]
  const pairs = pairAll(checkItems, scoreItems)
  const byId = new Map(scoreItems.map(item => [item.assessment_id, item]))
  const paired = new Set(pairs.values())
  const rows: Row[] = [
    ...checkItems.map(check => ({ kind: 'run' as const, id: check.assessment_id, date: check.created_at, check, quality: byId.get(pairs.get(check.assessment_id) ?? '') ?? null, live: live.get(check.assessment_id) })),
    ...scoreItems.filter(item => !paired.has(item.assessment_id)).map(item => ({ kind: 'quality' as const, id: item.assessment_id, date: item.created_at, quality: item })),
  ].filter(row => filter === 'all' || (filter === 'checks' ? row.kind === 'run' : row.kind === 'quality' || row.quality != null))
    .sort((a, b) => Date.parse(b.date) - Date.parse(a.date))
  const loading = checks.isLoading || scores.isLoading
  const error = checks.error ?? scores.error

  const modelOf = (row: Row) => {
    if (row.kind === 'quality') return { provider: row.quality.model.provider, name: row.quality.model.display_name }
    const listed = models.data?.models.find(item => item.model_id === row.check.model_id) ?? models.data?.pending.find(item => item.model_id === row.check.model_id)
    return listed ? { provider: listed.provider, name: listed.display_name }
      : row.quality ? { provider: row.quality.model.provider, name: row.quality.model.display_name } : { provider: '', name: row.check.model_id ?? '—' }
  }
  const open = (row: Row, part?: 'quality') => void (row.kind === 'quality'
    ? navigate({ to: '/score/$id', params: { id: row.id } })
    : navigate({ to: '/check/$id', params: { id: row.id }, search: part ? { part } : {} }))
  async function runQuality(event: MouseEvent, check: CredibilityHistoryItem) {
    event.stopPropagation()
    setBusy(check.assessment_id); setNotice(null)
    try {
      await startQuality(check.assessment_id, { arxivId: check.source_type === 'arxiv' ? check.arxiv_id : null, modelId: check.model_id })
      await queryClient.invalidateQueries({ queryKey: ['score-history'] })
      setNotice({ id: check.assessment_id, text: 'Quality assessment started; it usually takes about a minute.' })
    } catch (cause) {
      setNotice({ id: check.assessment_id, text: cause instanceof Error ? cause.message : 'The quality assessment could not be started.' })
    } finally { setBusy(null) }
  }
  async function remove(row: Row) {
    const qualityId = row.kind === 'quality' ? row.id : row.quality?.assessment_id ?? null
    setBusy(row.id); setNotice(null)
    try {
      await deleteRun(row.kind === 'run' ? row.id : null, qualityId)
      setAsking(null)
      if (row.kind === 'run') queryClient.removeQueries({ queryKey: ['check', row.id] })
      if (qualityId) queryClient.removeQueries({ queryKey: ['score', qualityId] })
      await Promise.all([queryClient.invalidateQueries({ queryKey: ['check-history'] }), queryClient.invalidateQueries({ queryKey: ['score-history'] })])
    } catch (cause) {
      setNotice({ id: row.id, error: true, text: cause instanceof ApiError && cause.code === 'STILL_RUNNING'
        ? 'Part of this run is still running; it can be deleted once it finishes.'
        : cause instanceof Error ? cause.message : 'The run could not be deleted.' })
    } finally { setBusy(null) }
  }

  return <main className="mx-auto grid w-full max-w-[1120px] gap-5 px-4 py-10 sm:px-6">
    <div className="grid gap-1"><p className="cs-eyebrow">History</p><h1 className="cs-serif text-title-l font-semibold">Assessment history</h1>
      <p className="cs-lead">One row per run: the credibility assessment and the quality assessment started with it. Opening a row does not re-run anything.</p></div>
    <div className="flex flex-wrap items-center gap-3">
      <label className="cs-find w-[320px]! bg-surface! shadow-[inset_0_0_0_1px_var(--color-line)]"><Icon name="search" />
        <input value={input} onChange={event => setInput(event.target.value)} placeholder="Search by title or arXiv identifier" aria-label="Search reports" /></label>
      <Segmented label="Show" value={filter} onChange={setFilter}
        options={[{ value: 'all', label: 'All' }, { value: 'checks', label: 'Credibility', icon: 'shield_checkmark' }, { value: 'scores', label: 'Quality (5-D)', icon: 'note' }]} />
    </div>
    <div className="cs-card overflow-x-auto">
      <table className="cs-history">
        <thead><tr><th>Paper</th><th>Model</th><th>Credibility</th><th>Quality (5-D)</th><th>Started</th>{canDelete && <th><span className="sr-only">Actions</span></th>}</tr></thead>
        <tbody>
          {rows.map(row => {
            const model = modelOf(row)
            const title = row.kind === 'run' ? row.check.title ?? row.check.arxiv_id ?? 'Uploaded PDF' : row.quality.title
            const source = row.kind === 'run' ? row.check.arxiv_id ?? 'PDF upload' : row.quality.source_type === 'arxiv' ? 'arXiv' : 'PDF upload'
            const key = `${row.kind}-${row.id}`
            const confirming = asking === key
            return <tr key={key} tabIndex={0} className={confirming ? 'is-confirm' : undefined} onClick={() => open(row)} onKeyDown={event => { if (event.key === 'Enter') open(row) }}>
              <td className="max-w-[340px]"><span className="cs-serif line-clamp-2 font-bold">{title}</span><span className="font-mono text-caption text-muted">{source}</span>
                {confirming && <span className="mt-1 block text-caption text-concern" role="alert">
                  {row.kind === 'run' && row.quality ? 'Deletes both assessments of this run from the server.' : 'Deletes this assessment from the server.'} This cannot be undone.</span>}
                {notice?.id === row.id && <span className={`mt-1 block text-caption ${notice.error ? 'text-concern' : 'text-accent-strong'}`} role="status">{notice.text}</span>}</td>
              <td><span className="inline-flex items-center gap-1.5 whitespace-nowrap text-small text-muted">{model.provider && <ProviderMark provider={model.provider} />}{model.name}</span></td>
              <td>{row.kind === 'quality' ? <span className="text-small text-slate-500">Not run</span>
                : row.live ? <Running assessment={row.live} />
                : row.check.error_code === 'CANCELLED' ? <span className="text-small font-bold text-slate-500">Cancelled</span>
                : row.check.status === 'failed' ? <span className="cs-hfail" title={row.check.error_code ?? undefined}>Failed<small>{errorTitles[row.check.error_code ?? ''] ?? row.check.error_code ?? ''}</small></span>
                : <Score value={row.check.overall_score} label={row.check.category ? categoryNames[row.check.category] : 'Not assessed'} onOpen={event => { event.stopPropagation(); open(row) }} />}</td>
              <td>{row.kind === 'quality'
                ? (row.quality.status === 'failed' ? <span className="cs-hfail">Failed</span>
                  : <Score value={row.quality.overall_score} label={rating(row.quality)} onOpen={event => { event.stopPropagation(); open(row) }} />)
                : row.quality == null
                  ? <span className="inline-flex items-center gap-2 text-small text-slate-500">Not run
                      {row.check.status === 'completed' && !row.live && <button type="button" className="cs-btn is-sm" disabled={busy === row.id} onClick={event => void runQuality(event, row.check)}>Run</button>}</span>
                  : row.quality.status === 'failed'
                    ? <span className="inline-flex items-center gap-2"><span className="cs-hfail">Failed</span>
                        <button type="button" className="cs-btn is-sm" disabled={busy === row.id} onClick={event => void runQuality(event, row.check)}><Icon name="arrow_clockwise" />Retry</button></span>
                    : <Score value={row.quality.overall_score} label={rating(row.quality)} onOpen={event => { event.stopPropagation(); open(row, 'quality') }} />}</td>
              <td className="whitespace-nowrap text-small text-muted tabular-nums">{startedAt(row.date)}</td>
              {canDelete && <td className="w-px whitespace-nowrap text-right" onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
                {row.kind === 'run' && row.live
                  ? <IconButton icon="delete" label="A running assessment cannot be deleted; cancel it on its page first" small className="is-delete" disabled />
                  : confirming
                  ? <span className="inline-flex gap-1.5">
                      <Button size="sm" disabled={busy === row.id} onClick={() => setAsking(null)}>Keep</Button>
                      <Button size="sm" className="is-danger" disabled={busy === row.id} onClick={() => void remove(row)}>{busy === row.id ? 'Deleting…' : 'Delete'}</Button>
                    </span>
                  : <IconButton icon="delete" label="Delete this run" small className="is-delete" onClick={() => { setAsking(key); setNotice(null) }} />}
              </td>}
            </tr>
          })}
        </tbody>
      </table>
      {loading && <p className="cs-lead p-4">Loading…</p>}
      {!loading && error && <p className="cs-lead p-4">The list could not be loaded: {error instanceof Error ? error.message : 'unknown error'}.</p>}
      {!loading && !error && !rows.length && <p className="cs-lead p-4">{search ? `Nothing matches “${search}”.` : 'No assessments recorded yet.'}</p>}
    </div>
  </main>
}
