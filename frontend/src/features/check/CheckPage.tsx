import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { credibilityApi } from '../../app/api'
import { closeTab, openTab } from '../../app/jobs'
import { usePairedQuality } from '../../app/pairing'
import { isRunning, useCheck } from '../../app/queries'
import { errorTitles, outOfScopeCodes } from '../../constants/labels'
import type { CredibilityAssessment } from '../../types/credibility'
import { Button } from '../../ui/Button'
import { Icon } from '../../ui/Icon'
import { transition } from '../../ui/motion'
import { ReportView } from '../report/ReportView'
import type { Reported } from '../report/widgets'
import { QualityPart, RunBar, type Part, type View } from './RunParts'
import { WaitingView } from './WaitingView'

type Kind = 'waiting' | 'report' | 'failed'
const kindOf = (value: CredibilityAssessment): Kind => (isRunning(value) ? 'waiting' : value.status === 'failed' || !value.report ? 'failed' : 'report')

function FailedView({ assessment }: { assessment: CredibilityAssessment }) {
  const navigate = useNavigate()
  const [retrying, setRetrying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const code = assessment.error?.code ?? ''
  const outOfScope = outOfScopeCodes.has(code)
  // A cancellation is the reader's own choice, so it reads neutrally rather than as a failure.
  const cancelled = code === 'CANCELLED'
  async function retry() {
    setRetrying(true); setError(null)
    try {
      const next = await credibilityApi.createAssessment({
        schema_version: assessment.schema_version, analysis_mode: 'credibility', source: assessment.source,
        model_id: assessment.model_id, rubric_version: assessment.report?.rubric_version ?? '0.2-provisional-complete',
      }, 'first_case')
      closeTab(assessment.assessment_id)
      openTab(next.assessment_id)
      await navigate({ to: '/check/$id', params: { id: next.assessment_id } })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The assessment could not be restarted.')
    } finally { setRetrying(false) }
  }
  return <main className="mx-auto grid w-full max-w-[720px] gap-4 px-4 py-16 sm:px-6">
    <section className="cs-card grid gap-3 p-6" style={{ viewTransitionName: `check-${assessment.assessment_id}` }}>
      <p className={`cs-eyebrow ${outOfScope || cancelled ? '' : 'text-concern'}`}>{cancelled ? 'Cancelled' : outOfScope ? 'Outside the assessment scope' : 'Assessment incomplete'}</p>
      <h1 className="cs-serif text-title-l font-semibold">{errorTitles[code] ?? 'Unable to assess this input'}</h1>
      {assessment.paper?.title && <p className="font-bold">{assessment.paper.title}</p>}
      <p>{assessment.error?.message}</p>
      <p className="cs-lead">No credibility score or risk category was produced.</p>
      {error && <p className="text-small text-concern" role="alert">{error}</p>}
      <div className="flex flex-wrap gap-2 pt-1">
        {assessment.error?.retryable && <Button variant="primary" icon="arrow_clockwise" disabled={retrying} onClick={() => void retry()}>{retrying ? 'Restarting…' : 'Retry assessment'}</Button>}
        <Link to="/" className="cs-btn">Assess another paper</Link>
      </div>
    </section>
  </main>
}

/** /check/$id: one address for a check, whatever its state (spec 5.2–5.3). */
export function CheckPage() {
  const { id } = useParams({ from: '/check/$id' })
  const check = useCheck(id)
  const value = check.data
  const kind = value ? kindOf(value) : null
  // When a running check finishes on screen, the progress gauge turns into the score.
  const [shown, setShown] = useState(kind)
  const finishing = shown === 'waiting' && kind != null && kind !== 'waiting'
  if (kind !== shown && !finishing) setShown(kind)
  useEffect(() => { if (finishing) transition(() => setShown(kind)) }, [finishing, kind])
  // Every opened check keeps a tab in the title bar until the reader closes it; an address that does not load gets none.
  const loaded = Boolean(value)
  useEffect(() => { if (loaded) openTab(id) }, [id, loaded])
  // A run has two peer parts: the credibility assessment and the quality assessment started beside it.
  const navigate = useNavigate()
  const search = useSearch({ from: '/check/$id' })
  // The two switches are independent: coming back to Credibility returns to the view that was left.
  const part: Part = search.part === 'quality' ? 'quality' : 'credibility'
  const readAs: View = search.view === 'report' ? 'report' : 'paper'
  const setPart = (next: Part) => void navigate({ to: '/check/$id', params: { id }, replace: true,
    search: prev => ({ ...prev, part: next === 'quality' ? 'quality' as const : undefined }) })
  const setView = (next: View) => void navigate({ to: '/check/$id', params: { id }, replace: true,
    search: prev => ({ ...prev, view: next === 'report' ? 'report' as const : undefined }) })
  // The reader's tools are rendered into the run bar, so the switches stay mounted (and slide) across parts.
  const [toolSlot, setToolSlot] = useState<HTMLElement | null>(null)
  const qualityId = usePairedQuality(value ? { assessment_id: value.assessment_id, created_at: value.created_at, title: value.paper?.title } : undefined)

  if (!value) {
    if (check.isError) return <main className="mx-auto grid max-w-[640px] gap-3 px-6 py-20 text-center">
      <h1 className="cs-serif text-title-l font-semibold">This assessment could not be opened</h1>
      <p className="cs-lead">{check.error instanceof Error ? check.error.message : 'The server did not return it.'}</p>
      <div><Link to="/" className="cs-btn"><Icon name="add" />New assessment</Link></div>
    </main>
    return <main className="grid place-items-center py-24 text-muted">Loading the assessment…</main>
  }
  const view = shown ?? kind
  const hasViews = part === 'credibility' && view !== 'failed' && !(view === 'waiting' && isRunning(value)) && Boolean(value.report)
  const bar = <RunBar assessment={value} qualityId={qualityId} part={part} onPart={setPart} view={readAs} onView={setView} hasViews={hasViews} toolSlot={setToolSlot} />
  if (part === 'quality') return <>{bar}<QualityPart assessment={value} qualityId={qualityId} /></>
  if (view === 'waiting' && isRunning(value)) return <>{bar}<WaitingView assessment={value} qualityId={qualityId} onQuality={() => setPart('quality')} /></>
  if (view === 'failed' || !value.report) return <>{bar}<FailedView assessment={value} /></>
  return <>{bar}<ReportView key={value.assessment_id} assessment={value as Reported} toolSlot={toolSlot} /></>
}
