import type { AssessmentStage } from '../types/assessment'

export const labels = {
  rating: {
    strong: 'Strong',
    good: 'Good',
    adequate: 'Adequate',
    weak: 'Weak',
    uncertain: 'Uncertain',
    not_applicable: 'Not applicable',
    unable_to_assess: 'Unable to assess',
  },
  confidence: { high: 'High confidence', medium: 'Medium confidence', low: 'Low confidence' },
  confidenceShort: { high: 'High', medium: 'Medium', low: 'Low' },
  recommendation: {
    worth_deep_reading: 'Worth reading in depth',
    worth_reading_with_checks: 'Worth reading, with checks',
    selective_reading: 'Read selectively',
    insufficient_evidence: 'Insufficient evidence',
    unable_to_assess: 'No recommendation possible',
  },
  stance: { supports: 'Supports', contradicts: 'Contradicts', context: 'Context' },
  support: {
    supported: 'Supported',
    partially_supported: 'Partially supported',
    unsupported: 'Unsupported',
    uncertain: 'Uncertain',
  },
  repro: {
    available: 'Provided',
    not_available: 'Not provided',
    unable_to_verify: 'Cannot verify',
    not_applicable: 'Not applicable',
  },
  importance: { major: 'Major', minor: 'Minor' },
  locator: {
    text: 'Passage',
    table: 'Table',
    figure: 'Figure',
    equation: 'Equation',
    theorem: 'Theorem',
  },
} as const

/** Short, plain-language titles for the backend error codes in contract v0.4. */
export const errorTitles: Record<string, string> = {
  INVALID_ARXIV_ID: 'That arXiv reference could not be read',
  ARXIV_NOT_FOUND: 'That paper was not found on arXiv',
  ARXIV_FETCH_FAILED: 'The paper could not be downloaded from arXiv',
  UNSUPPORTED_FILE_TYPE: 'That file type is not supported',
  FILE_TOO_LARGE: 'The file is too large',
  PAGE_LIMIT_EXCEEDED: 'The paper has too many pages',
  UPLOAD_NOT_FOUND: 'The uploaded PDF is no longer available',
  PDF_PASSWORD_PROTECTED: 'The PDF is password protected',
  PDF_PARSE_FAILED: 'The PDF could not be parsed',
  PARSE_FAILED: 'The parsed paper could not be read',
  EMPTY_TEXT: 'No readable text was found',
  SERVICE_RESTARTED: 'The service restarted before the check finished',
  UNSUPPORTED_LANGUAGE: 'The language is not supported',
  UNABLE_TO_ASSESS: 'This paper is outside the assessment scope',
  MODEL_UNAVAILABLE: 'The analysis model is unavailable',
  MODEL_TIMEOUT: 'The analysis model timed out',
  MODEL_OUTPUT_INVALID: 'The model returned an unusable report',
  INTERNAL_ERROR: 'The assessment could not be completed',
}

/**
 * `UNABLE_TO_ASSESS` is the ML-relevance gate declining a paper, not a fault.
 * It gets a neutral treatment rather than the red failure styling.
 */
export const outOfScopeCodes = new Set(['UNABLE_TO_ASSESS', 'UNSUPPORTED_LANGUAGE'])

export const stageDefinitions: Array<{
  stage: AssessmentStage
  label: string
  description: string
}> = [
  { stage: 'validating_input', label: 'Validate input', description: 'Checking the paper source, rubric and model selection.' },
  { stage: 'retrieving_paper', label: 'Retrieve paper', description: 'Fetching the paper file and its public metadata.' },
  { stage: 'parsing_paper', label: 'Parse paper', description: 'Extracting body text, sections and paper metadata.' },
  { stage: 'analyzing', label: 'Analyse', description: 'Scoring the paper across the five rubric dimensions.' },
  { stage: 'linking_evidence', label: 'Link evidence', description: 'Anchoring each judgement to pages, sections, tables and figures.' },
  { stage: 'finalizing_report', label: 'Assemble report', description: 'Validating the structure and settling the reading recommendation.' },
  { stage: 'report_ready', label: 'Report ready', description: 'The report has passed structural validation.' },
]

export function enumLabel<T extends Record<string, string>>(map: T, value: string) {
  return map[value as keyof T] ?? value
}

export type ScoreBand = 'strong' | 'good' | 'adequate' | 'weak' | 'none'

/** Score colour bands. Kept here so the meter, the numeral and the chips never disagree. */
export function scoreBand(score: number | null | undefined): ScoreBand {
  if (score == null) return 'none'
  if (score >= 8.5) return 'strong'
  if (score >= 7) return 'good'
  if (score >= 5.5) return 'adequate'
  return 'weak'
}

export function formatScore(score: number | null | undefined) {
  if (score == null) return '--'
  return Number.isInteger(score) ? String(score) : score.toFixed(1)
}

/**
 * Contract v0.4 §10.1 — five fixed dimensions in a fixed order.
 * Shown before submission so the reader knows what the score is made of.
 * The authoritative weights still come from the backend rubric in the report.
 */
export const rubricDimensions: Array<{ id: string; name: string; weight: number }> = [
  { id: 'research_value', name: 'Research Value & Significance', weight: 0.2 },
  { id: 'soundness_evidence', name: 'Soundness & Evidence Support', weight: 0.2 },
  { id: 'originality_positioning', name: 'Originality & Positioning', weight: 0.2 },
  { id: 'clarity_usability', name: 'Clarity & Usability', weight: 0.2 },
  { id: 'reproducibility_transparency', name: 'Reproducibility & Transparency', weight: 0.2 },
]

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
