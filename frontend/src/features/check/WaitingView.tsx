import { useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState, type CSSProperties } from 'react'
import { credibilityApi } from '../../app/api'
import { isRunning, useCheckHistory, useScoring } from '../../app/queries'
import { startQuality } from '../../app/quality'
import { categoryNames } from '../../constants/credibility'
import { labels } from '../../constants/labels'
import type { CredibilityAssessment } from '../../types/credibility'
import { ActivityLog } from '../../ui/ActivityLog'
import { Button } from '../../ui/Button'
import { Icon } from '../../ui/Icon'
import { ProgressDial } from '../../ui/ProgressDial'
import { Ticker } from '../../ui/Ticker'
import type { CheckLine, ProgressEvent } from './progress'
import { useLiveProgress } from './useLiveProgress'

const elapsedText = (seconds: number) => (seconds < 60 ? 'under 1 min elapsed' : `${Math.floor(seconds / 60)} min elapsed`)
const stateText: Record<string, string> = { completed: 'Complete', partial: 'Partial', failed: 'Failed', waiting: '' }
const lineText = (check: CheckLine) => (check.stopped ? 'Stopped' : check.stopping ? 'Stopping…'
  : check.state === 'running' ? `${Math.round(check.share * 100)}%` : stateText[check.state])

/**
 * Stops one check after a confirmation, for a check that runs far longer than the others. The other checks carry on;
 * the stopped one is recorded as stopped by the reader and receives the fallback score, never a finding.
 */
function StopCheck({ assessmentId, check, onClose }: { assessmentId: string; check: CheckLine; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function stop() {
    setBusy(true); setError(null)
    try {
      await credibilityApi.stopCheck!(assessmentId, check.id)
      await queryClient.invalidateQueries({ queryKey: ['check-progress', assessmentId] })
      onClose()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The check could not be stopped.')
      setBusy(false)
    }
  }
  return <div className="cs-stopask" role="group" aria-label={`Confirm stopping the ${check.name} check`}>
    <span>Stop the <b>{check.name}</b> check? It is recorded as stopped by you and receives the fallback score of 3,
      not a finding. The other checks continue.</span>
    <span className="flex gap-1.5">
      <Button size="sm" disabled={busy} onClick={onClose}>Keep running</Button>
      <Button size="sm" className="is-danger" disabled={busy} onClick={() => void stop()}>{busy ? 'Stopping…' : 'Stop check'}</Button>
    </span>
    {error && <span className="text-concern" role="alert">{error}</span>}
  </div>
}

/** The five dimensions in report order, with short names that fit beside a bar. */
const dimensions: [string, string][] = [
  ['research_value', 'Research value'], ['soundness_evidence', 'Soundness'], ['originality_positioning', 'Originality'],
  ['clarity_usability', 'Clarity'], ['reproducibility_transparency', 'Reproducibility'],
]

/**
 * The quality assessment, advertised while the credibility checks run (spec 5.2). It usually finishes first, so the
 * card says what it is and, once ready, invites the reader to open it in the Quality tab.
 */
function QualityCard({ assessment, qualityId, onOpen }: { assessment: CredibilityAssessment; qualityId: string | null; onOpen: () => void }) {
  const value = useScoring(qualityId).data
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const arxivId = assessment.source.type === 'arxiv' ? assessment.paper?.arxiv_id ?? assessment.source.arxiv_id : null
  async function run() {
    setStarting(true); setError(null)
    try { await startQuality(assessment.assessment_id, { arxivId, modelId: assessment.model_id }) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'The quality assessment could not be started.') }
    finally { setStarting(false) }
  }
  const overall = value?.report?.overall_assessment
  const ready = Boolean(overall)
  const percent = Math.round(value?.progress_percent ?? 0)
  const waiting = Boolean(qualityId) && (!value || isRunning(value))
  // What the assessment covers, as five empty tracks while it runs; they fill with the scores once it is ready.
  const tracks = <ul className="cs-qdims" aria-label={ready ? 'Dimension scores' : 'Dimensions assessed'}>{dimensions.map(([id, name]) => {
    const score = value?.report?.dimensions.find(item => item.dimension_id === id)?.score
    return <li key={id}><span>{name}</span>
      <span className={`is-track cs-groove ${waiting ? 'cs-shimmer' : ''}`}>{ready && score != null && <i className="cs-lit" style={{ width: `${score * 10}%` }} />}</span>
      <b>{ready ? score?.toFixed(1) ?? '—' : ''}</b></li>
  })}</ul>
  return <section className={`cs-card cs-qcard flex flex-col gap-3 p-5 ${ready ? 'is-ready' : ''}`} aria-label="Quality assessment">
    <p className="cs-eyebrow flex items-center gap-1.5"><Icon name="note" />Quality assessment · five dimensions{ready && <span className="cs-qready">Ready</span>}</p>
    {ready && overall ? <>
      <div className="flex items-baseline gap-3">
        <span className="cs-num text-[40px]">{overall.overall_score == null ? '—' : overall.overall_score.toFixed(1)}</span>
        <span className="grid"><b className="text-body-l">{labels.rating[overall.rating as keyof typeof labels.rating] ?? overall.rating}</b>
          <span className="text-small text-muted">{labels.recommendation[overall.reading_recommendation as keyof typeof labels.recommendation] ?? overall.reading_recommendation}</span></span>
      </div>
      {tracks}
      <div className="mt-auto"><Button variant="primary" icon="arrow_right" onClick={onOpen}>Read the quality assessment</Button></div>
    </> : <>
      <p className="text-body text-muted">A separate, faster reading of the paper's scholarly quality, with a reading recommendation. It does not change the credibility score.</p>
      {tracks}
      {/* Its progress is already a line in the breakdown above; here the button itself waits, then opens it. */}
      <div className="mt-auto">{!qualityId ? <Button icon="note" disabled={starting} onClick={() => void run()}>{starting ? 'Starting…' : 'Run quality assessment'}</Button>
        : value && !isRunning(value) ? <span className="grid justify-items-start gap-2">
          <span className="text-small text-concern">It did not complete: {value.error?.message ?? 'unknown error'}</span>
          <Button icon="arrow_clockwise" disabled={starting} onClick={() => void run()}>{starting ? 'Starting…' : 'Retry'}</Button>
        </span>
        : <button type="button" className="cs-btn cs-waitbtn" disabled aria-label={`Quality assessment ${percent}% done; it opens when ready`}>
          <span className="cs-tabpie" style={{ '--p': percent } as CSSProperties} aria-hidden="true" />Opens when ready · {percent}%</button>}</div>
    </>}
    {error && <p className="text-small text-concern" role="alert">{error}</p>}
  </section>
}

/** Other finished assessments to read while this one runs. */
function Browse({ exclude }: { exclude: string }) {
  const history = useCheckHistory()
  const items = (history.data?.items ?? []).filter(item => item.status === 'completed' && item.assessment_id !== exclude).slice(0, 4)
  return <section className="cs-card flex flex-col gap-2 p-5" aria-label="Recent assessments">
    <div className="flex items-baseline justify-between gap-3">
      <p className="cs-eyebrow">While you wait</p>
      <Link to="/reports" className="inline-flex items-center gap-1 text-small font-bold no-underline">Assessment history<Icon name="arrow_right" className="!size-3.5" /></Link>
    </div>
    {items.length ? <ul className="cs-browse">{items.map(item => <li key={item.assessment_id}>
      <Link to="/check/$id" params={{ id: item.assessment_id }}>
        <span className="cs-num">{item.overall_score == null ? '—' : item.overall_score.toFixed(1)}</span>
        <span className="min-w-0"><b>{item.title ?? item.arxiv_id ?? 'Uploaded PDF'}</b><small>{item.category ? categoryNames[item.category] : 'Not assessed'}</small></span>
      </Link>
    </li>)}</ul> : <p className="cs-lead">{history.isLoading ? 'Loading…' : 'No other completed assessments yet.'}</p>}
  </section>
}

/** Stops the run after a confirmation; the run stays in the history as cancelled and can be retried. */
function CancelButton({ assessment }: { assessment: CredibilityAssessment }) {
  const queryClient = useQueryClient()
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!credibilityApi.cancelAssessment) return null
  async function cancel() {
    setBusy(true); setError(null)
    try {
      const value = await credibilityApi.cancelAssessment!(assessment.assessment_id)
      queryClient.setQueryData(['check', assessment.assessment_id], value)
      void queryClient.invalidateQueries({ queryKey: ['check-history'] })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The assessment could not be cancelled.')
      setBusy(false)
    }
  }
  return asking
    ? <div className="cs-cancel" role="group" aria-label="Confirm cancellation">
        <span>Stop this assessment? It is kept in the history as cancelled and can be retried.</span>
        <span className="flex gap-1.5">
          <Button size="sm" disabled={busy} onClick={() => setAsking(false)}>Keep running</Button>
          <Button size="sm" className="is-danger" disabled={busy} onClick={() => void cancel()}>{busy ? 'Stopping…' : 'Stop'}</Button>
        </span>
        {error && <span className="text-concern" role="alert">{error}</span>}
      </div>
    : <button type="button" className="cs-cancel-link" onClick={() => setAsking(true)}><Icon name="dismiss" />Cancel assessment</button>
}

/** Waiting page (spec 5.2): one screen with the dial, the per-check breakdown and a scrolling log. */
export function WaitingView({ assessment, qualityId, onQuality }: { assessment: CredibilityAssessment; qualityId: string | null; onQuality: () => void }) {
  const view = useLiveProgress(assessment)!
  const scoring = useScoring(qualityId)
  const [asking, setAsking] = useState<CheckLine['id'] | null>(null)
  const askingLine = view.checks.find(check => check.id === asking && check.state === 'running' && !check.stopping)
  // The quality assessment runs beside the check; its event joins the log at the time it finished.
  const events: ProgressEvent[] = [...view.events]
  if (scoring.data?.status === 'completed') {
    events.push({ id: 'summary', text: 'Quality assessment available', state: 'done', at: Date.parse(scoring.data.completed_at ?? '') || 0 })
    events.sort((a, b) => a.at - b.at)
  }
  const title = assessment.paper?.title ?? scoring.data?.paper?.title ?? (assessment.source.type === 'arxiv' ? `arXiv:${assessment.source.arxiv_id}` : 'Uploaded PDF')

  return <main className="mx-auto grid w-full max-w-[1040px] gap-6 px-4 py-8 sm:px-6 sm:py-10">
    <section className="cs-card cs-glass grid items-center gap-6 p-5 sm:p-6 md:grid-cols-[minmax(0,250px)_minmax(0,1fr)]" style={{ viewTransitionName: `check-${assessment.assessment_id}` }}>
      <div className="grid justify-items-center gap-2">
        <ProgressDial percent={view.percent} label={view.stage} transitionName="score-gauge" />
        <div className="cs-dial2-caption" aria-live="polite">
          <b>{view.stage}</b>
          <Ticker lines={view.ticker} />
          <span>{view.remaining} · {elapsedText(view.elapsed)}</span>
        </div>
        <CancelButton assessment={assessment} />
      </div>
      <div className="grid content-start gap-4">
        <div className="grid gap-1">
          <p className="cs-eyebrow">Assessment in progress</p>
          <h1 className="cs-serif line-clamp-2 text-title-l font-semibold">{title}</h1>
        </div>
        {view.checks.length > 0 && <ul className="cs-breakdown" aria-label="Progress of each part">
          {view.checks.map(check => <li key={check.id} className={`is-${check.state} ${check.stopped || check.stopping ? 'is-stopped' : ''}`}>
            <Icon name={check.icon} /><span>{check.name}</span>
            <span className="is-bar"><i className={check.state === 'running' && !check.stopping ? 'cs-shimmer' : ''} style={{ width: `${Math.max(3, check.share * 100)}%` }} /></span>
            <span className="is-pct">{lineText(check)}</span>
            <span>{credibilityApi.stopCheck && check.state === 'running' && !check.stopping
              && <button type="button" className={`cs-stopcheck ${asking === check.id ? 'is-on' : ''}`} title={`Stop the ${check.name} check`}
                aria-label={`Stop the ${check.name} check`} aria-expanded={asking === check.id} onClick={() => setAsking(asking === check.id ? null : check.id)}>
                <Icon name="stop" /></button>}</span>
          </li>)}
          {/* The quality assessment runs in parallel and usually finishes first; it can be read straight away. */}
          {view.quality && <li className={`is-${view.quality.state}`}>
            <Icon name="note" /><span>Quality</span>
            <span className="is-bar"><i className={view.quality.state === 'running' ? 'cs-shimmer' : ''} style={{ width: `${Math.max(3, view.quality.share * 100)}%` }} /></span>
            <span className="is-pct">{view.quality.state === 'running' ? `${Math.round(view.quality.share * 100)}%` : view.quality.state === 'failed' ? 'Failed' : 'Ready'}</span>
            <span />
          </li>}
        </ul>}
        {askingLine && <StopCheck assessmentId={assessment.assessment_id} check={askingLine} onClose={() => setAsking(null)} />}
        <ActivityLog events={events} />
      </div>
    </section>
    <div className="grid gap-4 md:grid-cols-2">
      <QualityCard assessment={assessment} qualityId={qualityId} onOpen={onQuality} />
      <Browse exclude={assessment.assessment_id} />
    </div>
  </main>
}
