import { useEffect, useRef, useState } from 'react'
import type { AssessmentApi } from '../api/assessmentApi'
import { ApiError } from '../api/httpAssessmentApi'
import { credibilityModelChoice } from '../api/httpCredibilityApi'
import { mockCredibilityApi } from '../api/mockCredibilityApi'
import { duration } from '../constants/credibility'
import { errorTitles, labels, outOfScopeCodes } from '../constants/labels'
import type { Assessment, ModelOption } from '../types/assessment'
import { CREDIBILITY_RUBRIC_VERSION, CREDIBILITY_SCHEMA_VERSION, type CredibilityApi, type CredibilityAssessment, type CredibilityReport as Report, type CredibilityScenario } from '../types/credibility'
import { parseArxivInput } from '../utils/arxiv'
import { CredibilityReport } from './CredibilityReport'
import { ExampleReports } from './ExampleReports'
import './CredibilityWorkspace.css'

const scenarios: { id: CredibilityScenario; name: string; note: string }[] = [
  { id: 'first_case', name: 'First claim · prepared candidate', note: 'The paper_002 example. One supported claim; the overall assessment remains unavailable.' },
  { id: 'synthetic_complete', name: 'Core contradiction · synthetic', note: 'A critical finding stays visible beside the aggregate score.' },
  { id: 'external_failure', name: 'External lookup fails · synthetic', note: 'Keep completed checks and findings; leave the overall score empty.' },
  { id: 'not_applicable', name: 'Code not applicable · synthetic', note: 'Show the evidence for excluding a check, without treating it as a failed lookup.' },
  { id: 'parse_failure', name: 'Unreadable PDF · synthetic', note: 'Return an input error without a risk category or invented score.' },
]
const stages = ['queued', 'retrieving', 'parsing', 'checking', 'finalizing', 'done']
const stageNames: Record<string, string> = { queued: 'Queued', retrieving: 'Read source', parsing: 'Locate evidence', checking: 'Compare checks', finalizing: 'Prepare report', done: 'Ready' }

function pause(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const stop = () => { clearTimeout(timer); reject(new DOMException('Cancelled', 'AbortError')) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', stop); resolve() }, ms)
    signal.addEventListener('abort', stop, { once: true })
    if (signal.aborted) stop()
  })
}

const isRunning = (value: { status: string } | null | undefined) => value?.status === 'queued' || value?.status === 'processing'
const hasReport = (value: CredibilityAssessment | null): value is CredibilityAssessment & { report: Report } => Boolean(value?.report)

export interface RunningCheck { label: string; stage: string; progress: number }

interface Props {
  api?: CredibilityApi
  /** Five-dimension API for the quick paper score that can run beside a check. */
  scoringApi?: AssessmentApi
  pollingDelay?: number
  live?: boolean
  /** A saved or example check to show; the nonce lets the same report open twice. */
  openRequest?: { assessment: CredibilityAssessment; nonce: number } | null
  /** Changes when the user asks for the check home; a running check is never cancelled by it. */
  homeRequest?: number
  onOpenScoring?: (assessment: Assessment) => void
  onRunningChange?: (running: RunningCheck | null) => void
}

export function CredibilityWorkspace({
  api = mockCredibilityApi, scoringApi, pollingDelay, live = false, openRequest, homeRequest, onOpenScoring, onRunningChange,
}: Props) {
  const delay = pollingDelay ?? (live ? 2000 : 450)
  const [scenario, setScenario] = useState<CredibilityScenario>('first_case')
  const [sourceType, setSourceType] = useState<'arxiv' | 'pdf'>('arxiv')
  const [arxivInput, setArxivInput] = useState('2310.01798v1')
  const [pdf, setPdf] = useState<File | null>(null)
  const [assessment, setAssessment] = useState<CredibilityAssessment | null>(null)
  const [reading, setReading] = useState<CredibilityAssessment | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [models, setModels] = useState<ModelOption[]>([])
  const [modelId, setModelId] = useState<string | null>(null)
  const [offlineBackend, setOfflineBackend] = useState(false)
  const [modelsError, setModelsError] = useState<string | null>(null)
  const [alsoScore, setAlsoScore] = useState(true)
  const [scoring, setScoring] = useState<Assessment | null>(null)
  const [scoringError, setScoringError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const controller = useRef<AbortController | null>(null)
  const scoringController = useRef<AbortController | null>(null)
  const failedHeading = useRef<HTMLHeadingElement | null>(null)
  useEffect(() => () => { controller.current?.abort(); scoringController.current?.abort() }, [])
  useEffect(() => {
    if (!live || !api.getModels) return
    let active = true
    api.getModels().then(options => {
      if (!active) return
      const choice = credibilityModelChoice(options)
      setModels(choice.models); setModelId(choice.defaultId); setOfflineBackend(choice.offline)
      setModelsError(choice.defaultId ? null : 'No model is available for live checks. The backend needs an OpenAI or DeepSeek API key.')
    }).catch(cause => { if (active) setModelsError(cause instanceof Error ? cause.message : 'Model options could not be loaded.') })
    return () => { active = false }
  }, [api, live])

  const running = isRunning(assessment)
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  useEffect(() => {
    onRunningChange?.(running && assessment ? {
      label: assessment.paper?.title || (assessment.source.type === 'arxiv' ? assessment.source.arxiv_id : 'Uploaded PDF'),
      stage: stageNames[assessment.stage] ?? assessment.stage, progress: assessment.progress_percent,
    } : null)
  }, [assessment, running, onRunningChange])
  // Focus (and so scroll to) the progress heading once when a check is queued, and the failure when it ends.
  useEffect(() => {
    if (!reading && (assessment?.status === 'queued' || assessment?.status === 'failed')) failedHeading.current?.focus()
  }, [assessment?.status, reading])
  // A new open request replaces what is being read; adjusting state during render avoids an extra pass.
  const [seenOpen, setSeenOpen] = useState(openRequest)
  if (openRequest !== seenOpen) {
    setSeenOpen(openRequest)
    if (openRequest) setReading(openRequest.assessment)
  }
  const homeSeen = useRef(homeRequest)
  useEffect(() => {
    if (homeRequest === homeSeen.current) return
    homeSeen.current = homeRequest
    setReading(null)
    if (!controller.current) {
      scoringController.current?.abort()
      scoringController.current = null
      setAssessment(null); setScoring(null); setScoringError(null); setError(null)
    }
  }, [homeRequest])

  function reset() {
    controller.current?.abort()
    controller.current = null
    scoringController.current?.abort()
    scoringController.current = null
    setAssessment(null); setReading(null); setSubmitting(false); setError(null); setScoring(null); setScoringError(null)
  }

  async function score(arxivId: string, signal: AbortSignal) {
    if (!scoringApi) return
    try {
      const options = await scoringApi.getOptions()
      const venue = options.venues[0]
      const available = options.models.filter(item => item.is_available)
      const model = available.find(item => item.model_id === modelId) ?? available.find(item => item.is_default) ?? available[0]
      if (!venue || !model) throw new Error('Paper scoring has no available rubric or model.')
      const receipt = await scoringApi.createAssessment(sourceType === 'arxiv'
        ? { source_type: 'arxiv', arxiv_id: arxivId, venue_id: venue.venue_id, model_id: model.model_id }
        : { source_type: 'pdf', file: pdf!, venue_id: venue.venue_id, model_id: model.model_id })
      if (signal.aborted) return
      let next = await scoringApi.getAssessment(receipt.assessment_id, signal)
      setScoring(next)
      while (isRunning(next)) {
        await pause(delay, signal)
        next = await scoringApi.getAssessment(receipt.assessment_id, signal)
        if (signal.aborted) return
        setScoring(next)
      }
    } catch (cause) {
      if (!signal.aborted) setScoringError(cause instanceof Error ? cause.message : 'Paper scoring could not start.')
    }
  }

  async function run(event?: React.FormEvent) {
    event?.preventDefault()
    setError(null)
    const parsed = parseArxivInput(arxivInput)
    if (sourceType === 'arxiv' && !parsed.ok) { setError('Enter a valid arXiv ID or arxiv.org link.'); return }
    if (sourceType === 'pdf' && (!pdf || !pdf.name.toLowerCase().endsWith('.pdf'))) { setError(live ? 'Choose a PDF to check.' : 'Choose a PDF to preview this input path.'); return }
    if (live && !modelId) { setError(modelsError || 'Model options are still loading.'); return }
    if (!live && scenario === 'first_case' && (sourceType !== 'arxiv' || !parsed.ok || parsed.arxivId !== '2310.01798v1')) {
      setError('The prepared first case is locked to 2310.01798v1. Choose a synthetic scenario to preview another input.'); return
    }
    controller.current?.abort()
    scoringController.current?.abort()
    const current = new AbortController()
    controller.current = current
    setSubmitting(true); setReading(null); setScoring(null); setScoringError(null)
    try {
      const uploadId = sourceType === 'pdf' && live && api.uploadPdf && pdf ? await api.uploadPdf(pdf) : 'prototype-local-pdf'
      if (current.signal.aborted) return
      const request = {
        schema_version: CREDIBILITY_SCHEMA_VERSION, analysis_mode: 'credibility' as const,
        source: sourceType === 'arxiv'
          ? { type: 'arxiv' as const, arxiv_id: parsed.ok ? parsed.arxivId : '', upload_id: null }
          : { type: 'pdf' as const, arxiv_id: null, upload_id: uploadId },
        model_id: live ? modelId! : 'model_prototype_v1', rubric_version: CREDIBILITY_RUBRIC_VERSION,
      }
      let next = await api.createAssessment(request, scenario)
      if (current.signal.aborted) return
      setAssessment(next); setNow(Date.now())
      if (alsoScore && scoringApi) {
        scoringController.current?.abort()
        scoringController.current = new AbortController()
        void score(parsed.ok ? parsed.arxivId : '', scoringController.current.signal)
      }
      await follow(next, current)
    } catch (cause) {
      if (!current.signal.aborted) setError(cause instanceof Error ? cause.message : live ? 'The check could not be loaded.' : 'The preview could not be loaded.')
    } finally {
      if (controller.current === current) { controller.current = null; setSubmitting(false) }
    }
  }

  /** Poll until the check ends. A long check outlives brief network drops, so retry those with backoff. */
  async function follow(first: CredibilityAssessment, current: AbortController) {
    let next = first
    let failures = 0
    while (isRunning(next)) {
      await pause(failures ? delay * 2 ** failures : delay, current.signal)
      try {
        next = await api.getAssessment(next.assessment_id, current.signal)
        failures = 0
        setError(null)
      } catch (cause) {
        if (current.signal.aborted) return
        const permanent = cause instanceof ApiError && (cause.code === 'INVALID_RESPONSE' || (cause.status >= 400 && cause.status < 500 && cause.status !== 429))
        if (permanent || ++failures >= 5) throw cause
        setError(`${cause instanceof Error ? cause.message : 'The status could not be refreshed.'} Reconnecting…`)
        continue
      }
      if (current.signal.aborted) return
      setAssessment(next)
    }
  }

  async function resume() {
    if (!assessment) return
    const current = new AbortController()
    controller.current = current
    setError(null); setSubmitting(true)
    try {
      await follow(assessment, current)
    } catch (cause) {
      if (!current.signal.aborted) setError(cause instanceof Error ? cause.message : 'The check could not be loaded.')
    } finally {
      if (controller.current === current) { controller.current = null; setSubmitting(false) }
    }
  }

  const elapsed = assessment ? Math.max(0, (now - Date.parse(assessment.created_at)) / 1000) : 0

  function scoringCard() {
    if (!scoringApi || (!scoring && !scoringError && !alsoScore)) return null
    const overall = scoring?.report?.overall_assessment
    return <div className={`cred-scoring-card ${scoring?.status === 'completed' ? 'is-ready' : ''}`}>
      <span className="cred-kicker">Paper scoring · five dimensions</span>
      {scoringError ? <p>{scoringError}</p>
        : !scoring ? <p>Starting the quick score…</p>
        : isRunning(scoring) ? <p>Scoring the paper · {scoring.progress_percent}% · usually about a minute</p>
        : scoring.status === 'failed' ? <p>Paper scoring could not finish: {scoring.error?.message}</p>
        : <>
          <p><b>{overall?.overall_score == null ? 'Unscored' : `${overall.overall_score.toFixed(1)} / 10`}</b>{overall && ` · ${labels.rating[overall.rating as keyof typeof labels.rating] ?? overall.rating}`}</p>
          {onOpenScoring && <button className="button button-secondary button-sm" type="button" onClick={() => onOpenScoring(scoring)}>Read the scoring report →</button>}
        </>}
    </div>
  }

  function failed(value: CredibilityAssessment, onBack: () => void, backLabel: string) {
    const code = value.error?.code ?? ''
    return <section className="cred-failed">
      <p className="cred-kicker">{outOfScopeCodes.has(code) ? 'Outside the assessment scope' : 'Check could not be completed'}</p>
      <h1 ref={value === assessment ? failedHeading : undefined} tabIndex={-1}>{errorTitles[code] ?? 'Unable to assess this input'}</h1>
      {value.paper?.title && <p><b>{value.paper.title}</b></p>}
      <p>{value.error?.message}</p>
      <p className="cred-muted">No credibility score or risk category was produced.</p>
      <div className="cred-failed-actions">
        {value === assessment && value.error?.retryable && <button type="button" className="button button-primary" onClick={() => void run()}>{live ? 'Try again' : 'Preview a new attempt'}</button>}
        <button type="button" className="button button-secondary" onClick={onBack}>{backLabel}</button>
      </div>
    </section>
  }

  const note = live
    ? <div className="cred-prototype-note"><span className="cred-kicker">Live check · provisional rubric</span><p>Checks run on the server against the parsed paper. Scores are trial scores from a provisional rubric, not an approved assessment.</p></div>
    : <div className="cred-prototype-note"><span className="cred-kicker">Sample output · provisional rubric</span><p>No model, parser or external service runs here. The selected scenario controls the preview; inputs stay on this device.</p></div>

  if (reading) {
    return <div className="cred-main">
      <div className="cred-reading-bar" role="status">
        {running && assessment
          ? <><span>Your check is still running · {stageNames[assessment.stage]} · {assessment.progress_percent}%</span><button className="button button-primary button-sm" type="button" onClick={() => setReading(null)}>Back to progress</button></>
          : <><span>{reading.assessment_id === assessment?.assessment_id ? 'Your check' : 'Saved report'} · opened without running anything</span><button className="button button-secondary button-sm" type="button" onClick={() => setReading(null)}>Back</button></>}
      </div>
      {hasReport(reading)
        ? <CredibilityReport key={reading.assessment_id} assessment={reading} live={live} newLabel="Close" onNew={() => setReading(null)} />
        : failed(reading, () => setReading(null), 'Close')}
    </div>
  }

  return <div className="cred-main">
    {note}
    {!assessment && <>
      <div className="cred-page-heading"><p className="cred-kicker">Paper Credibility Check</p><h1>Check whether a paper’s claims hold up.</h1><p>Compare claims with the paper’s own evidence, verify its references, and look for code and reproduction reports.</p></div>
      <div className="cred-compose"><form className="cred-input" onSubmit={run}>
        <div className="cred-input-tabs" role="group" aria-label="Paper source"><button type="button" disabled={submitting} aria-pressed={sourceType === 'arxiv'} onClick={() => setSourceType('arxiv')}>arXiv</button><button type="button" disabled={submitting} aria-pressed={sourceType === 'pdf'} onClick={() => setSourceType('pdf')}>PDF</button></div>
        {sourceType === 'arxiv' ? <label className="cred-field">arXiv ID or link<input disabled={submitting} value={arxivInput} onChange={event => setArxivInput(event.target.value)} placeholder="2310.01798v1" autoComplete="off" /></label> : <label className="cred-field cred-upload">Choose a PDF<input disabled={submitting} type="file" accept="application/pdf,.pdf" onChange={event => setPdf(event.target.files?.[0] || null)} /><span>{pdf?.name || (live ? 'The PDF is uploaded and parsed on the server.' : 'A file selected here is not uploaded or parsed.')}</span></label>}
        {!live && <><label className="cred-field">Preview scenario<select disabled={submitting} value={scenario} onChange={event => { setScenario(event.target.value as CredibilityScenario); setError(null) }}>{scenarios.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <p className="cred-scenario-note">{scenarios.find(item => item.id === scenario)?.note}</p></>}
        {live && models.length > 1 && <label className="cred-field">Model<select disabled={submitting} value={modelId ?? ''} onChange={event => setModelId(event.target.value)}>{models.map(item => <option key={item.model_id} value={item.model_id}>{item.display_name}</option>)}</select></label>}
        {scoringApi && <label className="cred-check"><input type="checkbox" disabled={submitting} checked={alsoScore} onChange={event => setAlsoScore(event.target.checked)} /><span>Also score the paper on five dimensions<small>Ready in about a minute, so there is something to read while the check runs.</small></span></label>}
        {live && modelsError && <p className="field-error" role="alert">{modelsError}</p>}
        <div className="cred-input-footer"><span>{live ? offlineBackend ? 'Fixture backend · no model call' : models.find(item => item.model_id === modelId)?.display_name ?? 'Loading models…' : 'Sample model · no API call'}<br /><small>Rubric {CREDIBILITY_RUBRIC_VERSION}</small></span>
          <span className="cred-input-actions">{submitting && <button className="button button-ghost" type="button" onClick={reset}>Cancel</button>}<button className="button button-primary" type="submit" disabled={submitting || (live && !modelId)}>{submitting ? 'Preparing…' : live ? 'Run check' : 'Preview check'}<span aria-hidden="true"> →</span></button></span></div>
        {error && <p className="field-error" role="alert">{error}</p>}
      </form><aside className="cred-check-overview"><p className="cred-kicker">Three separate checks</p>{[
        ['01', 'Claims & evidence', 'Keep the claim and its supporting or conflicting passage together.'],
        ['02', 'References', 'Show what was extracted, checked, matched and left unverified.'],
        ['03', 'Code & reproduction', 'Keep source coverage, third-party feedback and author replies in context.'],
      ].map(([number,title,text]) => <div className="cred-overview-row" key={number}><span>{number}</span><div><h3>{title}</h3><p>{text}</p></div></div>)}<p className="cred-overview-foot">A check takes 5–25 minutes; the reference lookup is the slowest part. Any ML venue.</p></aside></div>
      <ExampleReports api={api} onOpen={setReading} />
    </>}
    {assessment && running && <div className="cred-wait">
      <section className="cred-progress"><p className="cred-kicker">{live ? 'Checking the paper' : 'Preparing a sample report'}</p><h1 ref={failedHeading} tabIndex={-1} aria-live="polite">{stageNames[assessment.stage]}</h1>
        <p>{live ? 'The three checks run side by side on the server. The page keeps polling while you read below or switch tabs.' : 'These stages preview the workflow; they do not perform an analysis.'}</p>
        <div className="cred-progress-track" role="progressbar" aria-label="Check progress" aria-valuenow={assessment.progress_percent} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${assessment.progress_percent}%` }} /></div>
        <ol>{stages.map((stage,index) => <li key={stage} className={index <= stages.indexOf(assessment.stage) ? 'is-current' : ''}><span>{String(index + 1).padStart(2,'0')}</span>{stageNames[stage]}</li>)}</ol>
        <p className="cred-elapsed">Running for {duration(elapsed)}{live && ' · usually 5–25 minutes'}{assessment.stage === 'checking' && live && ' · progress stays at 60% until all three checks finish'}</p>
        {error && <p role="alert">{error}</p>}
        <div className="cred-failed-actions">
          {error && !submitting && <button className="button button-primary" type="button" onClick={() => void resume()}>Resume polling</button>}
          <button className="button button-secondary" type="button" onClick={reset}>{live ? 'Cancel' : 'Cancel preview'}</button>
        </div>
      </section>
      <aside className="cred-wait-side" aria-label="While you wait">
        <h2>While you wait</h2>
        {scoringCard()}
        <ExampleReports api={api} onOpen={setReading} excludeId={assessment.assessment_id} limit={4} title="Read an example report" note="Finished checks on this server. The running check keeps going while you read." />
      </aside>
    </div>}
    {assessment?.status === 'failed' && failed(assessment, reset, 'Back to input')}
    {assessment?.status === 'completed' && hasReport(assessment) && <>
      {scoringCard()}
      <CredibilityReport key={assessment.assessment_id} assessment={assessment} live={live} scenario={live ? undefined : scenario} onNew={reset} />
    </>}
  </div>
}
