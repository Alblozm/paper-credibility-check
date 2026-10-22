import type { ModelOption } from './assessment'

/** Independent Paper Credibility Check (backend v1.0); never reuse the five-dimension report. */
export const CREDIBILITY_SCHEMA_VERSION = 'credibility-0.5.0-draft' as const
/** The provisional rubric gives trial scores; '0.1-draft' would leave every score empty. */
export const CREDIBILITY_RUBRIC_VERSION = '0.2-provisional-complete' as const
export type CredibilityRubricVersion = '0.1-draft' | typeof CREDIBILITY_RUBRIC_VERSION

export type CredibilityDimensionId = 'claim_evidence' | 'reference_verification' | 'code_reproduction'
export type CredibilityScenario =
  | 'first_case'
  | 'synthetic_complete'
  | 'external_failure'
  | 'not_applicable'
  | 'parse_failure'

export type CredibilitySource =
  | { type: 'arxiv'; arxiv_id: string; upload_id: null }
  | { type: 'pdf'; arxiv_id: null; upload_id: string }

export interface CreateCredibilityRequest {
  schema_version: typeof CREDIBILITY_SCHEMA_VERSION
  analysis_mode: 'credibility'
  source: CredibilitySource
  model_id: string
  rubric_version: CredibilityRubricVersion
}

export interface CredibilityPaper {
  paper_id: string
  title: string | null
  arxiv_id: string | null
  source_sha256: string | null
  page_count: number | null
  language: string | null
  parse_quality: 'good' | 'partial' | 'poor'
}

export interface CredibilityError {
  code: string
  message: string
  retryable: boolean
  debug_id: string | null
}

export interface CredibilityNotice {
  code: string
  message: string
  dimension_id: CredibilityDimensionId | null
}

export interface CredibilityAssessment {
  schema_version: typeof CREDIBILITY_SCHEMA_VERSION
  analysis_mode: 'credibility'
  assessment_id: string
  data_origin: 'live' | 'synthetic'
  source: CredibilitySource
  model_id: string
  status: 'queued' | 'processing' | 'completed' | 'failed'
  stage: 'queued' | 'retrieving' | 'parsing' | 'checking' | 'finalizing' | 'done' | 'failed'
  progress_percent: number
  created_at: string
  started_at: string | null
  completed_at: string | null
  paper: CredibilityPaper | null
  report: CredibilityReport | null
  warnings: CredibilityNotice[]
  error: CredibilityError | null
}

export interface CredibilityVersions {
  pipeline_version: string
  parser_version: string
  prompt_version: string
  /** One row per model call, e.g. claim_extraction, reference_source_agent, external_code_discovery_native_tools. */
  model_calls: Array<{ purpose: string; provider: string; model_version: string }>
}

export interface CredibilityDimension {
  dimension_id: CredibilityDimensionId
  applicability: 'applicable' | 'not_applicable' | 'undetermined'
  applicability_reason: string
  check_status: 'not_run' | 'running' | 'completed' | 'partial' | 'failed'
  score_status: 'pending' | 'scored' | 'unable_to_assess' | 'not_applicable'
  score: number | null
  weight: number
  score_rule_ids: string[]
  summary: string
  unassessed_reason: string | null
  evidence_ids: string[]
  finding_ids: string[]
}

export interface CredibilityPaperLocator {
  page_start: number
  page_end: number
  chunk_id: string
  section_title: string | null
  element_label: string | null
}

export interface CredibilityEvidence {
  evidence_id: string
  origin: 'paper' | 'external'
  mode: 'quote' | 'paraphrase'
  excerpt: string
  paper_locator: CredibilityPaperLocator | null
  source_id: string | null
  location_confidence: 'high' | 'medium' | 'low'
}

export interface CredibilityExternalSource {
  source_id: string
  kind: 'metadata' | 'supplement' | 'project_page' | 'repository' | 'discussion_listing' | 'issue' | 'comment'
  url: string
  fetch_status: 'ok' | 'not_found' | 'access_denied' | 'timeout' | 'rate_limited' | 'error'
  http_status: number | null
  retrieved_at: string
  observed_at: string
  is_cached: boolean
  snapshot_ref: string | null
  association_reason: string
}

export interface CredibilityFinding {
  finding_id: string
  dimension_id: CredibilityDimensionId
  item_ids: string[]
  kind: 'concern' | 'verification_gap' | 'context'
  severity: 'info' | 'minor' | 'moderate' | 'major' | 'critical'
  certainty: 'confirmed' | 'reported' | 'unresolved'
  title: string
  detail: string
  evidence_ids: string[]
  source_ids: string[]
  limitations: string[]
}

export interface CredibilityAlert {
  type: 'low_dimension_score' | 'material_finding' | 'assessment_gap'
  dimension_id: CredibilityDimensionId
  finding_ids: string[]
  message: string
}

export interface CredibilityClaimItem {
  claim_id: string
  statement: string
  importance: 'core' | 'supporting'
  conditions: string
  claim_evidence_ids: string[]
  comparison_evidence_ids: string[]
  search_scope: string
  outcome: 'supported' | 'partially_supported' | 'not_found' | 'contradicted' | 'unable_to_determine' | 'not_checked'
  reason: string
  score_rule_id: string | null
  item_score: number | null
  /** The original judgment, kept when code evidence changed the outcome of an artifact claim. */
  reconciled_from?: CredibilityClaimReconciliation | null
}

export interface CredibilityClaimReconciliation {
  outcome: 'partially_supported' | 'not_found' | 'unable_to_determine'
  reason: string
  comparison_evidence_ids: string[]
  search_scope: string
  score_rule_id: string | null
  item_score: number | null
  findings: CredibilityFinding[]
  repository_ids: string[]
}

export interface CredibilityClaimChecks {
  selection_scope: string
  scope_complete: boolean
  scope_limitations: string[]
  items: CredibilityClaimItem[]
}

export interface CredibilityBibliographicMetadata {
  title: string | null
  authors: string[]
  year: number | null
  doi: string | null
  arxiv_id: string | null
}

export interface CredibilityReferenceCandidate {
  candidate_id: string
  metadata: CredibilityBibliographicMetadata
  source_id: string
  evidence_ids: string[]
  match_basis: string
}

export interface CredibilityReferenceItem {
  reference_id: string
  raw_text: string
  paper_evidence_ids: string[]
  parsed: CredibilityBibliographicMetadata
  outcome: 'matched' | 'metadata_conflict' | 'unverified' | 'not_checked'
  reason: string
  source_ids: string[]
  candidates: CredibilityReferenceCandidate[]
  selected_candidate_id: string | null
  score_rule_id: string | null
  item_score: number | null
}

export interface CredibilityReferenceChecks {
  extraction_status: 'complete' | 'partial' | 'failed' | 'not_applicable'
  extraction_reason: string
  original_total: number | null
  extracted_count: number
  checked_count: number
  matched_count: number
  conflict_count: number
  unverified_count: number
  not_checked_count: number
  query_completion_ratio: number | null
  extraction_coverage_ratio: number | null
  gaps: string[]
  items: CredibilityReferenceItem[]
}

export interface CredibilityCodeSearch {
  scope_id: string
  scope_type: 'paper_body' | 'appendix' | 'supplement' | 'project_page' | 'repository' | 'discussion_list'
  outcome: 'checked' | 'not_present' | 'not_applicable' | 'failed' | 'not_checked'
  reason: string
  evidence_ids: string[]
  source_ids: string[]
}

export interface CredibilityRepository {
  repository_id: string
  source_id: string
  association: 'confirmed' | 'uncertain' | 'rejected'
  association_reason: string
  association_evidence_ids: string[]
  availability: 'accessible' | 'inaccessible' | 'unknown'
  implementation_evidence_ids: string[]
  inspected_revision: string | null
}

export interface CredibilityDiscussion {
  discussion_id: string
  repository_id: string
  source_ids: string[]
  evidence_ids: string[]
  signal: 'success_reported' | 'failure_reported' | 'discussion_only'
  relevance: 'relevant' | 'irrelevant' | 'undetermined'
  independence: 'independent' | 'author' | 'unknown'
  conditions: string
  resolution: 'open' | 'resolved' | 'disputed' | 'unknown'
  author_response_source_ids: string[]
  scoring_eligible: boolean
  reason: string
}

export interface CredibilityCodeChecks {
  scope_complete: boolean
  scope_limitations: string[]
  searches: CredibilityCodeSearch[]
  code_availability: 'available' | 'not_found_in_scope' | 'unable_to_verify' | 'not_applicable'
  reproduction_signal: 'success_reported' | 'failure_reported' | 'discussion_only' | 'no_feedback_found' | 'unable_to_verify' | 'not_applicable'
  repositories: CredibilityRepository[]
  discussions: CredibilityDiscussion[]
  base_score_rule_id: string | null
  score_rule_id: string | null
}

export type ReferenceUnresolvedReasonCode =
  | 'target_not_found_in_scope' | 'year_mismatch' | 'author_mismatch' | 'version_evidence_missing'
  | 'source_fetch_failed' | 'insufficient_metadata' | 'personal_communication' | 'extraction_gap'
  | 'metadata_conflict' | 'budget_exhausted' | 'identifier_mismatch' | 'invalid_arxiv_id' | 'title_mismatch'
  | 'not_checked' | 'undetermined_reason'

/** Observed verification, kept apart from the trial score. */
export interface CredibilityReferenceVerificationSummary {
  verification_rate: number | null
  denominator: 'extracted_references'
  extracted_count: number
  matched_count: number
  unverified_count: number
  not_checked_count: number
  metadata_conflict_count: number
  extraction_complete: boolean
  unresolved_items: Array<{
    reference_id: string
    outcome: 'metadata_conflict' | 'unverified' | 'not_checked'
    reason_code: ReferenceUnresolvedReasonCode
    reason: string
    source_ids: string[]
  }>
}

/** How completely each check ran; separate from its trial score. */
export interface CredibilityCoverage {
  dimension_id: CredibilityDimensionId
  check_status: string
  scope_complete: boolean
  population: 'selected_claims' | 'extracted_references' | 'bounded_repository_search'
  inspected_count: number
  total_count: number | null
  completion_ratio: number | null
  extraction_coverage_ratio: number | null
  outcomes: Record<string, number>
  confirmed_concern_count: number
  unresolved_count: number
  technical_failure_count: number
  fallback_rule_ids: string[]
  limitations: string[]
}

export type CredibilityCheckName = 'claim_check' | 'reference_check' | 'code_check'

/** The three checks run as parallel branches; each records its own outcome and time. */
export interface CredibilityExecution {
  workers?: number
  checks?: Partial<Record<CredibilityCheckName, {
    status: 'completed' | 'partial' | 'failed'
    reused: boolean
    elapsed_seconds: number
    error?: { code: string; retryable: boolean }
  }>>
}

export interface CredibilityExternalReview {
  mode: string
  status: string
  audit_ref: string | null
  warnings: string[]
  base_facts_reused: boolean
}

export interface CredibilityCodeDiscovery {
  status: 'not_run' | 'disabled' | 'not_needed' | 'completed' | 'partial' | 'failed'
  reason: string
  scope_complete: false
  queries: Array<{
    query_id: string
    /** openai_web_search with GPT, deepseek_search with DeepSeek. */
    provider: string
    query: string
    status: 'completed' | 'partial' | 'failed' | 'not_run'
    result_count: number
    source_ids: string[]
    reason: string
  }>
  candidates: Array<{
    candidate_id: string
    url: string
    title: string | null
    resource_kind: 'implementation_candidate' | 'official_implementation' | 'third_party_reproduction'
      | 'third_party_related' | 'reading_list' | 'undetermined'
    status: 'candidate' | 'verified' | 'rejected'
    repository_id: string | null
    query_ids: string[]
    source_ids: string[]
    evidence_ids: string[]
    reason: string
  }>
  budget: {
    max_queries: number
    queries_used: number
    max_candidates: number
    candidates_inspected: number
    max_source_reads: number
    source_reads_used: number
    max_model_rounds: number
    model_rounds_used: number
    max_tool_calls: number
    tool_calls_used: number
  }
  limitations: string[]
  audit_ref: string | null
}

export interface CredibilityOverall {
  status: 'assessed' | 'unable_to_assess'
  score: number | null
  category: 'no_significant_concerns' | 'some_concerns' | 'significant_concerns' | 'critical_concerns' | null
  summary: string
  unassessed_reason: string | null
  calculation: {
    method: 'equal_weight_applicable_dimensions' | 'equal_weight_trial_dimensions'
    included_dimension_ids: CredibilityDimensionId[]
    excluded_dimension_ids: CredibilityDimensionId[]
    blocking_dimension_ids: CredibilityDimensionId[]
    assessed_dimension_count: number
    /** Trial scoring only: dimensions whose score came from a fallback rule for an incomplete check. */
    fallback_dimension_ids?: CredibilityDimensionId[]
  }
}

export interface CredibilityReport {
  report_id: string
  rubric_id: 'paper_credibility'
  rubric_version: CredibilityRubricVersion
  rubric_status: 'draft' | 'provisional' | 'approved'
  rubric_sha256?: string | null
  scope_complete: boolean
  is_provisional: boolean
  versions: CredibilityVersions
  generated_at: string
  dimensions: [CredibilityDimension, CredibilityDimension, CredibilityDimension]
  checks: {
    claims: CredibilityClaimChecks
    references: CredibilityReferenceChecks
    code: CredibilityCodeChecks
  }
  evidence: CredibilityEvidence[]
  sources: CredibilityExternalSource[]
  findings: CredibilityFinding[]
  overall: CredibilityOverall
  alerts: CredibilityAlert[]
  limitations: string[]
  coverage: CredibilityCoverage[]
  execution: CredibilityExecution
  external_review: CredibilityExternalReview | null
  reference_verification_summary: CredibilityReferenceVerificationSummary
  code_discovery: CredibilityCodeDiscovery
}

export interface CredibilityHistoryItem {
  assessment_id: string
  status: 'completed' | 'failed'
  title: string | null
  arxiv_id: string | null
  source_type: 'arxiv' | 'pdf' | null
  model_id: string | null
  overall_score: number | null
  category: CredibilityOverall['category']
  error_code: string | null
  created_at: string
  completed_at: string | null
}

export interface CredibilityHistory {
  items: CredibilityHistoryItem[]
  total: number
  offset: number
  limit: number
}

export interface CredibilityApi {
  /** The scenario is honoured only by the local mock; the HTTP adapter ignores it. */
  createAssessment(request: CreateCredibilityRequest, scenario: CredibilityScenario): Promise<CredibilityAssessment>
  getAssessment(assessmentId: string, signal?: AbortSignal): Promise<CredibilityAssessment>
  /** Present only when the API can store a PDF and return an upload ID. */
  uploadPdf?(file: File): Promise<string>
  /** Completed and failed checks, newest first. */
  listAssessments?(search?: string, offset?: number): Promise<CredibilityHistory>
  /** Backend model options, unfiltered; see credibilityModelChoice. */
  getModels?(): Promise<ModelOption[]>
}

