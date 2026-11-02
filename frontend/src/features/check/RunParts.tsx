import { useState, type CSSProperties, type ReactNode } from 'react'
import { isRunning, useModels, useScoring } from '../../app/queries'
import { startQuality } from '../../app/quality'
import { categoryNames, startedAt } from '../../constants/credibility'
import { errorTitles, labels, stageDefinitions } from '../../constants/labels'
import type { CredibilityAssessment } from '../../types/credibility'
import { Button } from '../../ui/Button'
import { Segmented } from '../../ui/Controls'
import { Icon, type IconName } from '../../ui/Icon'
import { ProviderMark } from '../../ui/ProviderMark'
import { QualityReport, type Scored } from '../score/QualityReport'
import { useLiveProgress } from './useLiveProgress'

export type Part = 'credibility' | 'quality'
export type View = 'paper' | 'report'

/** One state for a part of a run: what the tab says and how it looks. */
interface PartState { tone: 'running' | 'done' | 'failed' | 'idle'; text: string; detail?: string; percent?: number }

function PartLabel({ icon, name, state }: { icon: IconName; name: string; state: PartState }) {
  return <span className={`cs-part is-${state.tone}`}>
    <Icon name={icon} /><b>{name}</b>
    {state.tone === 'running' && <span className="cs-tabpie" style={{ '--p': state.percent ?? 0 } as CSSProperties} aria-hidden="true" />}
    <span className="is-state">{state.text}{state.detail && <span className="is-detail"> · {state.detail}</span>}</span>
  </span>
}

/** Who ran it and when: tells two runs of the same paper apart. */
function useRunMeta(assessment: CredibilityAssessment) {
  const models = useModels()
  const model = models.data?.models.find(item => item.model_id === assessment.model_id)
  return { provider: model?.provider ?? '', name: model?.display_name ?? assessment.model_id, started: startedAt(assessment.created_at) }
}

interface RunBarProps {
  assessment: CredibilityAssessment
  qualityId: string | null
  part: Part
  onPart: (part: Part) => void
  view: View
  onView: (view: View) => void
  /** Only a finished credibility report has two views; elsewhere the view switch folds away. */
  hasViews: boolean
  /** Where the reader puts its tools (page, zoom, search, layers), so switches and tools share one bar. */
  toolSlot: (node: HTMLElement | null) => void
}

/**
 * Two switches for a run (spec 5.3). The first picks the part: the credibility assessment or the quality assessment
 * started beside it, each with its own state, so a part that failed or was not run is visible from the other. The
 * second picks how the credibility report is read: the paper with highlights, or the report as one document.
 */
export function RunBar({ assessment, qualityId, part, onPart, view, onView, hasViews, toolSlot }: RunBarProps) {
  const live = useLiveProgress(isRunning(assessment) ? assessment : undefined)
  const quality = useScoring(qualityId).data
  const meta = useRunMeta(assessment)
  const overall = assessment.report?.overall
  const credibility: PartState = isRunning(assessment) ? { tone: 'running', text: `${Math.round(live?.percent ?? 0)}%`, percent: Math.round(live?.percent ?? 0) }
    : assessment.error?.code === 'CANCELLED' ? { tone: 'idle', text: 'Cancelled' }
    : assessment.status === 'failed' || !assessment.report ? { tone: 'failed', text: 'Failed' }
    : { tone: 'done', text: overall?.score?.toFixed(1) ?? '—', detail: overall?.category ? categoryNames[overall.category] : 'Not assessed' }
  const qualityRating = quality?.report?.overall_assessment
  const qualityState: PartState = !qualityId ? { tone: 'idle', text: 'Not run' }
    : !quality || isRunning(quality) ? { tone: 'running', text: `${quality?.progress_percent ?? 0}%`, percent: quality?.progress_percent ?? 0 }
    : quality.status === 'failed' || !qualityRating ? { tone: 'failed', text: 'Failed' }
    : { tone: 'done', text: qualityRating.overall_score?.toFixed(1) ?? '—', detail: labels.rating[qualityRating.rating as keyof typeof labels.rating] ?? qualityRating.rating }
  return <div className="cs-runbar">
    <Segmented size="lg" label="Part of this run" value={part} onChange={onPart} options={[
      { value: 'credibility', label: <PartLabel icon="shield_checkmark" name="Credibility" state={credibility} />, title: 'Credibility assessment: claims, references, code' },
      { value: 'quality', label: <PartLabel icon="note" name="Quality" state={qualityState} />, title: 'Five-dimension quality assessment' },
    ]} />
    <span className={`cs-runbar-view ${hasViews ? '' : 'is-folded'}`} inert={!hasViews}>
      <span><Segmented size="lg" label="View" value={view} onChange={onView} options={[
        { value: 'paper', icon: 'document_pdf', label: <span className="is-name">Paper</span>, title: 'The paper with the evidence highlighted' },
        { value: 'report', icon: 'text_bullet_list_ltr', label: <span className="is-name">Report</span>, title: 'The assessment as one document' },
      ]} /></span>
    </span>
    <span ref={toolSlot} className="cs-runbar-tools" />
    <span className="cs-runmeta" title={`Run with ${meta.name}, started ${meta.started}`}>
      {meta.provider && <ProviderMark provider={meta.provider} />}<span>{meta.name}</span><span className="max-sm:hidden">· {meta.started}</span>
    </span>
  </div>
}

/** The Quality tab: the report when it exists, otherwise what happened and what can be done. */
export function QualityPart({ assessment, qualityId }: { assessment: CredibilityAssessment; qualityId: string | null }) {
  const scoring = useScoring(qualityId)
  const value = scoring.data
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const arxivId = assessment.source.type === 'arxiv' ? assessment.paper?.arxiv_id ?? assessment.source.arxiv_id : null
  async function run() {
    setStarting(true); setError(null)
    try { await startQuality(assessment.assessment_id, { arxivId, modelId: assessment.model_id }) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'The quality assessment could not be started.') }
    finally { setStarting(false) }
  }
  const panel = (eyebrow: string, title: string, body: ReactNode, action?: ReactNode) =>
    <main className="mx-auto grid w-full max-w-[720px] gap-4 px-4 py-12 sm:px-6">
      <section className="cs-card grid gap-3 p-6">
        <p className="cs-eyebrow">{eyebrow}</p>
        <h1 className="cs-serif text-title-l font-semibold">{title}</h1>
        {body}
        {error && <p className="text-small text-concern" role="alert">{error}</p>}
        {action && <div className="flex flex-wrap gap-2 pt-1">{action}</div>}
      </section>
    </main>

  if (!qualityId) return panel('Quality assessment · five dimensions', 'No quality assessment was run with this check',
    <p className="cs-lead">The quality assessment rates the paper's research value, soundness, originality, clarity and reproducibility, and gives a reading recommendation. It is independent of the credibility score and usually takes about a minute.</p>,
    <Button variant="primary" icon="note" disabled={starting} onClick={() => void run()}>{starting ? 'Starting…' : 'Run quality assessment'}</Button>)
  if (!value) return <main className="grid place-items-center py-24 text-muted">{scoring.isError ? 'The quality assessment could not be loaded.' : 'Loading the quality assessment…'}</main>
  if (isRunning(value)) {
    const stage = stageDefinitions.find(item => item.stage === value.stage)
    return panel('Quality assessment in progress', 'Assessing scholarly quality', <>
      <div className="flex items-center gap-3">
        <span className="cs-minibar h-1.5 flex-1"><i className="cs-shimmer" style={{ width: `${Math.max(4, value.progress_percent)}%` }} /></span>
        <span className="cs-num text-title-s">{Math.round(value.progress_percent)}%</span>
      </div>
      <p className="text-body">{stage ? `${stage.label}: ${stage.description}` : 'Working on the assessment.'}</p>
      <p className="cs-lead">Usually ready within a minute. The credibility assessment continues in its own tab.</p>
    </>)
  }
  if (value.status === 'failed' || !value.report) return panel('Quality assessment incomplete', errorTitles[value.error?.code ?? ''] ?? 'The quality assessment did not complete',
    <><p>{value.error?.message}</p><p className="cs-lead">The credibility assessment is not affected. Retrying starts a new quality assessment for this run.</p></>,
    value.error?.retryable !== false && <Button variant="primary" icon="arrow_clockwise" disabled={starting} onClick={() => void run()}>{starting ? 'Starting…' : 'Retry quality assessment'}</Button>)
  return <QualityReport assessment={value as Scored} embedded />
}
