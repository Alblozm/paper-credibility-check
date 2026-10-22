export type Confidence = 'high' | 'medium' | 'low'
export type AssessmentStatus = 'queued' | 'processing' | 'completed' | 'failed'
export type AssessmentStage =
  | 'validating_input'
  | 'retrieving_paper'
  | 'parsing_paper'
  | 'analyzing'
  | 'linking_evidence'
  | 'finalizing_report'
  | 'report_ready'

export type MockScenario = 'completed' | 'provisional' | 'retryable_error' | 'non_retryable_error'

export interface VenueOption {
  venue_id: string
  code: string
  name: string
  year: number
  track: string
  rubric_id: string
  rubric_version: string
  is_available: boolean
}

export interface ModelOption {
  model_id: string
  display_name: string
  provider: string
  model_version: string
  description: string
  speed_tier: string
  is_available: boolean
  is_default: boolean
}

export interface EvidenceLink {
  evidence_id: string
  stance: 'supports' | 'contradicts' | 'context'
  reason: string
}

export interface EvidenceReference {
  evidence_id: string
  page_number: number | null
  section_id: string | null
  section_title: string | null
  locator_type: 'text' | 'table' | 'figure' | 'equation' | string
  locator_label: string | null
  evidence_mode: 'quote' | 'paraphrase'
  excerpt: string
  location_confidence: Confidence
}

export interface DimensionAssessment {
  dimension_id: string
  display_name: string
  applicability: 'applicable' | 'uncertain' | 'not_applicable'
  score: number | null
  rating: 'strong' | 'good' | 'adequate' | 'weak' | 'uncertain' | 'not_applicable'
  weight: number
  summary: string
  confidence: Confidence
  uncertainty_reason: string | null
  evidence_links: EvidenceLink[]
}

export interface Finding {
  finding_id: string
  title: string
  detail: string
  importance: 'major' | 'minor'
  confidence: Confidence
  evidence_links: EvidenceLink[]
}

export interface ClaimCheck {
  claim_id: string
  claim: string
  support_level: 'supported' | 'partially_supported' | 'unsupported' | 'uncertain'
  reasoning: string
  confidence: Confidence
  evidence_links: EvidenceLink[]
}

export interface ReproducibilityCheck {
  check_id: string
  display_name: string
  status: 'available' | 'not_available' | 'unable_to_verify' | 'not_applicable'
  detail: string
  evidence_links: EvidenceLink[]
}

export interface UnresolvedQuestion {
  question_id: string
  question: string
  why_it_matters: string
  evidence_links: EvidenceLink[]
}

export interface AssessmentReport {
  paper_summary: string
  claimed_contributions: Array<{
    contribution_id: string
    statement: string
    contribution_type: string
    evidence_links: EvidenceLink[]
  }>
  claim_checks: ClaimCheck[]
  dimensions: DimensionAssessment[]
  reproducibility_checklist: ReproducibilityCheck[]
  strengths: Finding[]
  concerns: Finding[]
  unresolved_questions: UnresolvedQuestion[]
  overall_assessment: {
    overall_score: number | null
    rating: string
    summary: string
    reading_recommendation: string
    recommendation_reason: string
    confidence: Confidence
    is_provisional: boolean
    calculation: {
      method: string
      assessed_dimension_count: number
      excluded_dimension_ids: string[]
    }
  }
  evidence: EvidenceReference[]
  limitations: string[]
  generated_at: string
}

export interface AssessmentError {
  code: string
  message: string
  stage: AssessmentStage | null
  retryable: boolean
  debug_id: string | null
}

export interface Assessment {
  schema_version: string
  assessment_id: string
  status: AssessmentStatus
  stage: AssessmentStage
  progress_percent: number
  source: {
    type: 'arxiv' | 'pdf'
    arxiv_id: string | null
    original_url: string | null
    upload_id: string | null
    original_filename: string | null
  }
  venue: Omit<VenueOption, 'is_available'>
  model: Pick<ModelOption, 'model_id' | 'display_name' | 'provider' | 'model_version'>
  paper: {
    paper_id: string
    title: string | null
    authors: string[]
    abstract: string | null
    language: string | null
    page_count: number | null
    arxiv_id: string | null
    doi: string | null
    sections: unknown[]
    parse_quality: string
    parse_warnings: string[]
  } | null
  report: AssessmentReport | null
  warnings: Array<{ code: string; message: string; stage: AssessmentStage | null }>
  error: AssessmentError | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface AssessmentOptions {
  venues: VenueOption[]
  models: ModelOption[]
}

export interface ReviewSummary {
  assessment_id: string
  status: 'completed' | 'failed'
  title: string
  authors: string[]
  source_type: 'arxiv' | 'pdf'
  venue: Assessment['venue']
  model: Assessment['model']
  overall_score: number | null
  rating: string | null
  is_demo: boolean
  created_at: string
  completed_at: string | null
}

export interface ReviewList {
  items: ReviewSummary[]
  total: number
  offset: number
  limit: number
}

export type CreateAssessmentRequest =
  | { source_type: 'arxiv'; arxiv_id: string; venue_id: string; model_id: string }
  | { source_type: 'pdf'; file: File; venue_id: string; model_id: string }

export interface SelectedEvidence {
  reference: EvidenceReference
  link: EvidenceLink
}

/**
 * The drawer opens on one evidence item but keeps the whole set the reader
 * clicked from, so they can step through a judgement's evidence without
 * closing and re-opening the panel.
 */
export interface EvidenceSelection {
  items: SelectedEvidence[]
  index: number
  context: string
}

declare global {
  interface Window {
    PCC_MOCK: {
      venues: VenueOption[]
      models: ModelOption[]
      stages: Array<{ stage: AssessmentStage; label: string; description: string; progress: number }>
      errors: { retryable: AssessmentError; nonRetryable: AssessmentError }
      completed: Assessment
      provisional: Assessment
    }
  }
}
