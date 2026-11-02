import { Link, Navigate, useParams } from '@tanstack/react-router'
import { usePairedCheck } from '../../app/pairing'
import { isRunning, useScoring } from '../../app/queries'
import { errorTitles, outOfScopeCodes, stageDefinitions } from '../../constants/labels'
import { Icon } from '../../ui/Icon'
import { QualityReport, type Scored } from './QualityReport'

/** A quality assessment run on its own (no credibility check beside it): progress, failure or the report. */
export function ScorePage() {
  const { id } = useParams({ from: '/score/$id' })
  const scoring = useScoring(id)
  const value = scoring.data
  // A quality assessment that belongs to a run opens as that run's Quality tab, beside its credibility assessment.
  const run = usePairedCheck(id)
  if (run) return <Navigate to="/check/$id" params={{ id: run }} search={{ part: 'quality' }} replace />
  if (run === undefined) return <main className="grid place-items-center py-24 text-muted">Loading the quality assessment…</main>

  if (!value) return scoring.isError
    ? <main className="mx-auto grid max-w-[640px] gap-3 px-6 py-20 text-center">
        <h1 className="cs-serif text-title-l font-semibold">This quality assessment could not be opened</h1>
        <p className="cs-lead">{scoring.error instanceof Error ? scoring.error.message : 'The server did not return it.'}</p>
        <div><Link to="/reports" className="cs-btn"><Icon name="history" />Assessment history</Link></div>
      </main>
    : <main className="grid place-items-center py-24 text-muted">Loading the quality assessment…</main>

  if (isRunning(value)) {
    const stage = stageDefinitions.find(item => item.stage === value.stage)
    return <main className="mx-auto grid w-full max-w-[720px] gap-4 px-4 py-16 sm:px-6">
      <section className="cs-card cs-glass grid gap-3 p-6">
        <p className="cs-eyebrow">Quality assessment in progress</p>
        <h1 className="cs-serif text-title-l font-semibold">{value.paper?.title ?? value.source.arxiv_id ?? value.source.original_filename ?? 'Paper'}</h1>
        <div className="flex items-center gap-3">
          <span className="cs-minibar h-1.5 flex-1"><i className="cs-shimmer" style={{ width: `${Math.max(4, value.progress_percent)}%` }} /></span>
          <span className="cs-num text-title-s tabular-nums">{Math.round(value.progress_percent)}%</span>
        </div>
        <p className="text-body">{stage ? `${stage.label}: ${stage.description}` : 'Working on the assessment.'}</p>
        <p className="cs-lead">Typically completes within a minute. This page updates on its own.</p>
      </section>
    </main>
  }

  if (value.status === 'failed' || !value.report) {
    const code = value.error?.code ?? ''
    const outOfScope = outOfScopeCodes.has(code)
    return <main className="mx-auto grid w-full max-w-[720px] gap-4 px-4 py-16 sm:px-6">
      <section className="cs-card grid gap-3 p-6">
        <p className={`cs-eyebrow ${outOfScope ? '' : 'text-concern'}`}>{outOfScope ? 'Outside the assessment scope' : 'Quality assessment incomplete'}</p>
        <h1 className="cs-serif text-title-l font-semibold">{errorTitles[code] ?? 'Unable to assess this input'}</h1>
        {value.paper?.title && <p className="font-bold">{value.paper.title}</p>}
        {value.error?.message && <p>{value.error.message}</p>}
        <p className="cs-lead">No quality score or reading recommendation was produced.</p>
        <div className="flex flex-wrap gap-2 pt-1">
          <Link to="/" className="cs-btn is-primary"><Icon name="add" />New assessment</Link>
          <Link to="/reports" className="cs-btn"><Icon name="history" />Assessment history</Link>
        </div>
      </section>
    </main>
  }

  return <QualityReport assessment={value as Scored} />
}
