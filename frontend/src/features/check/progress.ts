import type { CheckProgress, CheckState } from '../../app/api'
import type { CredibilityAssessment, CredibilityCheckName } from '../../types/credibility'
import type { IconName } from '../../ui/Icon'

const order = ['queued', 'retrieving', 'parsing', 'checking', 'finalizing', 'done'] as const
type Stage = typeof order[number]
const reached = (assessment: CredibilityAssessment, stage: Stage) =>
  assessment.status === 'completed' || order.indexOf(assessment.stage as Stage) >= order.indexOf(stage)

/** Typical GPT durations in seconds; DeepSeek runs about 2.5 times longer. */
const expected: Record<CredibilityCheckName, number> = { claim_check: 150, reference_check: 420, code_check: 60 }
const slowdown = (modelId: string) => (/deepseek/i.test(modelId) ? 2.5 : 1)
/** Estimated share done: approaches but never reaches 90% (design spec 4.1). */
const estimate = (seconds: number, typical: number) => 0.9 * (1 - Math.exp(-Math.max(0, seconds) / (typical * 0.5)))

const checkMeta: Record<CredibilityCheckName, { name: string; icon: IconName }> = {
  claim_check: { name: 'Claims', icon: 'text_quote' },
  reference_check: { name: 'References', icon: 'book_open' },
  code_check: { name: 'Code', icon: 'code' },
}

/** Backend step names (model call purposes) in plain words, for the one-line ticker. */
const stepText: Record<string, string> = {
  claim_extraction: 'extracting the principal claims',
  claim_extraction_repair: 'verifying claim quotations against the text',
  claim_judgment: 'evaluating claims against the evidence',
  claim_judgment_repair: 'evaluating claims against the evidence',
  artifact_release_classification: 'classifying statements about released artefacts',
  reference_field_parsing: 'parsing bibliographic entries',
  reference_source_agent: 'resolving unmatched references',
  paper_link_classification: 'classifying links cited in the paper',
  external_code_discovery_native_tools: 'searching for code repositories',
  external_code_native_tools_output_retry: 'searching for code repositories',
  external_code_prefetch_review: 'reviewing repositories and discussions',
  external_code_prefetch_review_repair: 'reviewing repositories and discussions',
  external_reproduction_report_check: 'searching for independent reproduction reports',
}
const fallbackStep: Record<CredibilityCheckName, string> = {
  claim_check: 'comparing claims with in-paper evidence',
  reference_check: 'verifying bibliographic references',
  code_check: 'searching for code and reproduction reports',
}
const describeStep = (step: string) => stepText[step] ?? (/^[a-z0-9_ -]+$/i.test(step) ? step.replaceAll('_', ' ') : 'processing the assessment')

export type EventState = 'done' | 'running' | 'partial' | 'failed'
export interface ProgressEvent { id: string; text: string; state: EventState; at: number }
export interface CheckLine {
  id: CredibilityCheckName
  name: string
  icon: IconName
  state: CheckState
  share: number
  /** The reader asked to stop it and it has not ended yet. */
  stopping: boolean
  /** It ended because the reader stopped it. */
  stopped: boolean
}

export interface ProgressView {
  percent: number
  stage: string
  /** "About 3–7 min left", or the usual total before the checks start. */
  remaining: string
  /** Per-check breakdown, present once the checks have started. */
  checks: CheckLine[]
  /** Major events only, oldest first; steps that have not been reached are not listed. */
  events: ProgressEvent[]
  /** What each running check is doing now, one line each. */
  ticker: string[]
}

const minutes = (seconds: number) => Math.max(1, Math.round(seconds / 60))
const ids: CredibilityCheckName[] = ['claim_check', 'reference_check', 'code_check']

/**
 * Turns the coarse stage, the per-check events and sub-steps (when the backend reports them) and
 * elapsed time into what the waiting page shows. Every event is real; only shares are estimates.
 */
export function describeProgress(assessment: CredibilityAssessment, progress: CheckProgress | null | undefined, now: number, checkingSince: number | null): ProgressView {
  const factor = slowdown(assessment.model_id)
  const created = Date.parse(assessment.created_at)
  const started = Date.parse(assessment.started_at ?? assessment.created_at)
  const checkStart = checkingSince ?? started + 30_000
  const inChecks = reached(assessment, 'checking')
  const pastChecks = reached(assessment, 'finalizing')

  const events: ProgressEvent[] = [{ id: 'queued', text: 'Request queued', state: assessment.stage === 'queued' ? 'running' : 'done', at: created }]
  if (reached(assessment, 'retrieving')) {
    const fetched = reached(assessment, 'parsing')
    events.push(assessment.source.type === 'arxiv'
      ? { id: 'source', text: `${fetched ? 'Retrieved' : 'Retrieving'} arXiv:${assessment.source.arxiv_id}`, state: fetched ? 'done' : 'running', at: started }
      : { id: 'source', text: 'Received the uploaded PDF', state: 'done', at: started })
  }
  if (reached(assessment, 'parsing')) events.push({ id: 'parse', text: inChecks ? 'Parsed sections, claims and references' : 'Parsing the paper', state: inChecks ? 'done' : 'running', at: started + 1 })

  const checks: CheckLine[] = []
  const remainders: number[] = []
  let weighted = 0, weights = 0
  for (const id of ids) {
    const reported = progress?.checks?.[id]
    let state: CheckState = !inChecks ? 'waiting' : reported?.status ?? (pastChecks ? 'completed' : 'running')
    if (pastChecks && (state === 'running' || state === 'waiting')) state = 'completed'
    const ownStart = reported?.started_at ? Date.parse(reported.started_at) : checkStart
    const typical = expected[id] * factor
    const share = state === 'running' ? estimate((now - ownStart) / 1000, typical) : state === 'waiting' ? 0 : 1
    // Weighted by typical duration: references take far longer than code, so they move the dial less per second.
    weighted += share * expected[id]
    weights += expected[id]
    if (state === 'running') remainders.push(typical - (now - ownStart) / 1000)
    const stopped = Boolean(reported?.stopped_by_user)
    if (inChecks) checks.push({ id, ...checkMeta[id], state, share, stopping: state === 'running' && Boolean(reported?.stop_requested), stopped })
    if (reported?.finished_at && state !== 'running') {
      const suffix = state === 'partial' ? ' (partial coverage)' : state === 'failed' ? ' (not completed)' : ''
      events.push(stopped
        ? { id: `${id}-done`, text: `${checkMeta[id].name} check stopped by you`, state: 'failed', at: Date.parse(reported.finished_at) }
        : { id: `${id}-done`, text: `${checkMeta[id].name} check complete${suffix}`, state: state === 'completed' ? 'done' : state === 'partial' ? 'partial' : 'failed', at: Date.parse(reported.finished_at) })
    }
  }
  if (inChecks) {
    const firstStart = Math.min(...ids.map(id => { const at = progress?.checks?.[id]?.started_at; return at ? Date.parse(at) : checkStart }))
    events.push({ id: 'checks', text: 'Claims, references and code checks started', state: 'done', at: firstStart })
  }
  if (pastChecks && !progress) events.push({ id: 'checks-done', text: 'All three checks complete', state: 'done', at: now - 1 })
  if (assessment.stage === 'finalizing') events.push({ id: 'report', text: 'Compiling the report', state: 'running', at: now })
  if (assessment.status === 'completed') events.push({ id: 'ready', text: 'Report available', state: 'done', at: now })
  events.sort((a, b) => a.at - b.at)

  const doneCount = checks.filter(check => check.share === 1).length
  const percent = assessment.status === 'completed' ? 100
    : assessment.stage === 'finalizing' ? 94
    : assessment.stage === 'checking' ? 25 + 65 * weighted / weights
    : assessment.stage === 'parsing' ? 18 : assessment.stage === 'retrieving' ? 8 : 2
  const stage = assessment.stage === 'queued' ? 'Queued'
    : assessment.stage === 'retrieving' ? 'Retrieving the paper'
    : assessment.stage === 'parsing' ? 'Parsing the paper'
    : assessment.stage === 'checking' ? `${doneCount} of 3 checks complete`
    : assessment.stage === 'finalizing' ? 'Compiling the report' : 'Complete'

  // Checks run side by side, so the slowest one sets the time left; the range reflects how much runs vary.
  let remaining = factor > 1 ? 'Typical duration 15–30 min' : 'Typical duration 5–15 min'
  if (assessment.stage === 'finalizing') remaining = 'Less than 1 min remaining'
  else if (remainders.length) {
    const left = Math.max(...remainders) + 30
    const low = minutes(left * 0.7), high = Math.max(low + 1, minutes(left * 1.5))
    remaining = left <= 60 ? 'Exceeding the typical duration' : `Estimated ${low}–${high} min remaining`
  }

  const ticker = checks.filter(check => check.state === 'running').map(check => {
    const last = progress?.checks?.[check.id]?.last_step
    return `${check.name} — ${last ? describeStep(last) : fallbackStep[check.id]}`
  })
  return { percent, stage, remaining, checks, events, ticker }
}
