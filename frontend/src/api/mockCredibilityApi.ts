import {
  CREDIBILITY_RUBRIC_VERSION,
  CREDIBILITY_SCHEMA_VERSION,
  type CreateCredibilityRequest,
  type CredibilityApi,
  type CredibilityAssessment,
  type CredibilityCodeChecks,
  type CredibilityDimension,
  type CredibilityDimensionId,
  type CredibilityEvidence,
  type CredibilityExternalSource,
  type CredibilityPaper,
  type CredibilityReferenceChecks,
  type CredibilityReferenceVerificationSummary,
  type CredibilityReport,
  type CredibilityScenario,
} from '../types/credibility'

// Local fixtures only: no fetch, upload, parser, model, or external tool call.
const PAPER_002_SHA = 'f204bfe2e642ab52abbf3fa94141e644d22a23510d6d64ed8ddc0ee9823eb983'
const dimensionIds: CredibilityDimensionId[] = ['claim_evidence', 'reference_verification', 'code_reproduction']
const steps = [
  { stage: 'retrieving', progress: 18 },
  { stage: 'parsing', progress: 36 },
  { stage: 'checking', progress: 64 },
  { stage: 'finalizing', progress: 92 },
  { stage: 'done', progress: 100 },
] as const
const records = new Map<string, { assessment: CredibilityAssessment; scenario: CredibilityScenario; tick: number }>()
let sequence = 0

const clone = <T,>(value: T): T => structuredClone(value)
const timestamp = () => new Date().toISOString()

function paperEvidence(id: string, excerpt: string, page: number, chunk: string, element: string | null = null): CredibilityEvidence {
  return {
    evidence_id: id,
    origin: 'paper',
    mode: 'paraphrase',
    excerpt,
    paper_locator: { page_start: page, page_end: page, chunk_id: chunk, section_title: null, element_label: element },
    source_id: null,
    location_confidence: 'high',
  }
}

function externalEvidence(id: string, excerpt: string, sourceId: string): CredibilityEvidence {
  return { evidence_id: id, origin: 'external', mode: 'paraphrase', excerpt, paper_locator: null, source_id: sourceId, location_confidence: 'high' }
}

function externalSource(id: string, kind: CredibilityExternalSource['kind'], path: string, reason: string, now: string): CredibilityExternalSource {
  return {
    source_id: id,
    kind,
    url: `https://example.invalid/${path}`,
    fetch_status: 'ok',
    http_status: 200,
    retrieved_at: now,
    observed_at: now,
    is_cached: false,
    snapshot_ref: `synthetic_snapshot_${id}`,
    association_reason: reason,
  }
}

function dimension(id: CredibilityDimensionId, score: number, rule: string, summary: string, evidenceIds: string[]): CredibilityDimension {
  return {
    dimension_id: id,
    applicability: 'applicable',
    applicability_reason: 'This check applies within the fictional fixture scope.',
    check_status: 'completed',
    score_status: 'scored',
    score,
    weight: 1,
    score_rule_ids: [rule],
    summary,
    unassessed_reason: null,
    evidence_ids: evidenceIds,
    finding_ids: [],
  }
}

function emptyReferences(reason: string): CredibilityReferenceChecks {
  return {
    extraction_status: 'failed',
    extraction_reason: reason,
    original_total: null,
    extracted_count: 0,
    checked_count: 0,
    matched_count: 0,
    conflict_count: 0,
    unverified_count: 0,
    not_checked_count: 0,
    query_completion_ratio: null,
    extraction_coverage_ratio: null,
    gaps: [reason],
    items: [],
  }
}

function emptyCode(reason: string): CredibilityCodeChecks {
  return {
    scope_complete: false,
    scope_limitations: [reason],
    searches: ['paper_body', 'appendix', 'supplement'].map((scope) => ({
      scope_id: `scope_${scope}`,
      scope_type: scope as 'paper_body' | 'appendix' | 'supplement',
      outcome: 'not_checked',
      reason,
      evidence_ids: [],
      source_ids: [],
    })),
    code_availability: 'unable_to_verify',
    reproduction_signal: 'unable_to_verify',
    repositories: [],
    discussions: [],
    base_score_rule_id: null,
    score_rule_id: null,
  }
}

/** Mirrors the backend summary: every non-matched item is unresolved, against extracted references. */
function referenceSummary(checks: CredibilityReferenceChecks): CredibilityReferenceVerificationSummary {
  return {
    verification_rate: checks.extracted_count ? checks.matched_count / checks.extracted_count : null,
    denominator: 'extracted_references',
    extracted_count: checks.extracted_count,
    matched_count: checks.matched_count,
    unverified_count: checks.unverified_count,
    not_checked_count: checks.not_checked_count,
    metadata_conflict_count: checks.conflict_count,
    extraction_complete: checks.extraction_status === 'complete' && !checks.gaps.length,
    unresolved_items: checks.items.filter(item => item.outcome !== 'matched').map(item => ({
      reference_id: item.reference_id,
      outcome: item.outcome as 'metadata_conflict' | 'unverified' | 'not_checked',
      reason_code: item.outcome === 'metadata_conflict' || item.outcome === 'not_checked' ? item.outcome : 'undetermined_reason',
      reason: item.reason,
      source_ids: [...item.source_ids],
    })),
  }
}

function reportShell(id: string, now: string): CredibilityReport {
  return {
    report_id: `synthetic_report_${id}`,
    rubric_id: 'paper_credibility',
    rubric_version: CREDIBILITY_RUBRIC_VERSION,
    rubric_status: 'provisional',
    scope_complete: false,
    is_provisional: true,
    versions: {
      pipeline_version: 'prototype-fixture-v1-no-pipeline-run',
      parser_version: 'prototype-prepared-snapshot-no-parser-run',
      prompt_version: 'prototype-fixture-no-prompt-run',
      model_calls: [],
    },
    generated_at: now,
    dimensions: [
      dimension('claim_evidence', 0, 'C_CORE_CONTRADICTION', 'The fictional core claim conflicts with its result table.', ['ev_claim', 'ev_result']),
      dimension('reference_verification', 10, 'R_MATCHED', 'The single fictional reference has a unique matching metadata candidate.', ['ev_reference', 'ev_metadata']),
      dimension('code_reproduction', 9, 'K_AVAILABLE', 'Fictional implementation files are available; no relevant reproduction feedback was found.', ['ev_code_link', 'ev_code_files', 'ev_discussions']),
    ],
    checks: {
      claims: { selection_scope: '', scope_complete: false, scope_limitations: [], items: [] },
      references: emptyReferences('Reference extraction has not been simulated.'),
      code: emptyCode('Code checks have not been simulated.'),
    },
    evidence: [],
    sources: [],
    findings: [],
    overall: {
      status: 'assessed',
      score: 6.3,
      category: 'some_concerns',
      summary: 'Fictional results: the 6.3 average does not remove the critical core-claim contradiction.',
      unassessed_reason: null,
      calculation: {
        method: 'equal_weight_applicable_dimensions',
        included_dimension_ids: [...dimensionIds],
        excluded_dimension_ids: [],
        blocking_dimension_ids: [],
        assessed_dimension_count: 3,
      },
    },
    alerts: [],
    coverage: [],
    execution: {},
    external_review: null,
    reference_verification_summary: referenceSummary(emptyReferences('Reference extraction has not been simulated.')),
    code_discovery: {
      status: 'not_run', reason: 'Prototype fixture: no external code discovery runs.', scope_complete: false, queries: [], candidates: [],
      budget: { max_queries: 0, queries_used: 0, max_candidates: 0, candidates_inspected: 0, max_source_reads: 0, source_reads_used: 0, max_model_rounds: 0, model_rounds_used: 0, max_tool_calls: 0, tool_calls_used: 0 },
      limitations: [], audit_ref: null,
    },
    limitations: ['Prototype fixture only. No paper was downloaded or parsed, no external URL was fetched, and no model was called.', 'Draft scoring rules have not been calibrated or accepted.'],
  }
}

function syntheticReport(id: string, now: string): CredibilityReport {
  const report = reportShell(id, now)
  const metadata = { title: 'Synthetic Reference A', authors: ['Example Author'], year: 2025, doi: null, arxiv_id: null }
  report.checks.claims = {
    selection_scope: 'The only abstract performance claim in a fictional six-page paper.',
    scope_complete: true,
    scope_limitations: ['The paper, numbers, evidence, and sources are invented for UI checks.'],
    items: [{
      claim_id: 'claim_01',
      statement: 'On Dataset A, the proposed method improves accuracy from 80% to 84%.',
      importance: 'core',
      conditions: 'Dataset A, accuracy in percent, baseline 80%, identical fictional evaluation conditions.',
      claim_evidence_ids: ['ev_claim'],
      comparison_evidence_ids: ['ev_result'],
      search_scope: 'The entire fictional paper, including the abstract, methods, and Table 1.',
      outcome: 'contradicted',
      reason: 'Table 1 gives 78% for the proposed method and 80% for the baseline under matching conditions, directly contradicting the abstract.',
      score_rule_id: 'C_CORE_CONTRADICTION',
      item_score: 0,
    }],
  }
  report.checks.references = {
    extraction_status: 'complete',
    extraction_reason: 'The fictional reference section has one entry, with checked boundaries and no missing fragments.',
    original_total: 1,
    extracted_count: 1,
    checked_count: 1,
    matched_count: 1,
    conflict_count: 0,
    unverified_count: 0,
    not_checked_count: 0,
    query_completion_ratio: 1,
    extraction_coverage_ratio: 1,
    gaps: [],
    items: [{
      reference_id: 'ref_01',
      raw_text: 'Example Author. Synthetic Reference A. 2025.',
      paper_evidence_ids: ['ev_reference'],
      parsed: clone(metadata),
      outcome: 'matched',
      reason: 'The fictional candidate matches title, author, and year.',
      source_ids: ['src_metadata'],
      candidates: [{ candidate_id: 'candidate_01', metadata: clone(metadata), source_id: 'src_metadata', evidence_ids: ['ev_metadata'], match_basis: 'Matching fictional title, author, and year.' }],
      selected_candidate_id: 'candidate_01',
      score_rule_id: 'R_MATCHED',
      item_score: 10,
    }],
  }
  report.checks.code = {
    scope_complete: true,
    scope_limitations: ['Only the explicitly linked fictional repository and its fictional discussions are included. No code was executed.'],
    searches: [
      { scope_id: 'scope_body', scope_type: 'paper_body', outcome: 'checked', reason: 'The fictional body names the code repository.', evidence_ids: ['ev_code_link'], source_ids: [] },
      { scope_id: 'scope_appendix', scope_type: 'appendix', outcome: 'not_present', reason: 'The complete fictional six-page paper contains no appendix.', evidence_ids: [], source_ids: [] },
      { scope_id: 'scope_supplement', scope_type: 'supplement', outcome: 'not_present', reason: 'The fictional input manifest and body confirm there are no supplemental materials.', evidence_ids: [], source_ids: [] },
      { scope_id: 'scope_repository', scope_type: 'repository', outcome: 'checked', reason: 'The fictional repository contains the described implementation.', evidence_ids: ['ev_code_files'], source_ids: ['src_repo'] },
      { scope_id: 'scope_discussions', scope_type: 'discussion_list', outcome: 'checked', reason: 'The fictional discussion list has one installation question, no result reproduction feedback, and no further pages.', evidence_ids: ['ev_discussions'], source_ids: ['src_discussions'] },
    ],
    code_availability: 'available',
    reproduction_signal: 'no_feedback_found',
    repositories: [{
      repository_id: 'repo_01', source_id: 'src_repo', association: 'confirmed',
      association_reason: 'The fictional body links this repository directly.', association_evidence_ids: ['ev_code_link'],
      availability: 'accessible', implementation_evidence_ids: ['ev_code_files'], inspected_revision: 'synthetic_revision_001',
    }],
    discussions: [{
      discussion_id: 'discussion_01', repository_id: 'repo_01', source_ids: ['src_issue', 'src_reply'], evidence_ids: ['ev_issue', 'ev_reply'],
      signal: 'discussion_only', relevance: 'irrelevant', independence: 'unknown',
      conditions: 'A fictional dependency installation problem; no experiment or accuracy result is reported.',
      resolution: 'resolved', author_response_source_ids: ['src_reply'], scoring_eligible: false,
      reason: 'The fictional author response identifies a missing dependency and the follow-up confirms installation. It does not establish result reproduction success or failure.',
    }],
    base_score_rule_id: 'K_AVAILABLE',
    score_rule_id: 'K_AVAILABLE',
  }
  report.evidence = [
    paperEvidence('ev_claim', 'Fictional abstract: Dataset A accuracy improves from baseline 80% to proposed method 84%.', 1, 'synthetic_chunk_001'),
    paperEvidence('ev_result', 'Fictional Table 1: under matched Dataset A evaluation, baseline accuracy is 80% and proposed method accuracy is 78%.', 4, 'synthetic_chunk_004', 'Table 1'),
    paperEvidence('ev_reference', 'Example Author. Synthetic Reference A. 2025.', 6, 'synthetic_chunk_006'),
    externalEvidence('ev_metadata', 'Fictional metadata: Synthetic Reference A, Example Author, 2025.', 'src_metadata'),
    paperEvidence('ev_code_link', 'Fictional body: Code is linked at https://example.invalid/repository/synthetic-paper.', 1, 'synthetic_chunk_001'),
    externalEvidence('ev_code_files', 'Fictional repository contents include method implementation files. They have not been executed.', 'src_repo'),
    externalEvidence('ev_discussions', 'Fictional discussion listing: one installation question, no relevant reproduction reports, and no additional pages.', 'src_discussions'),
    externalEvidence('ev_issue', 'Fictional user: installation fails before any experiment is run because a dependency is missing.', 'src_issue'),
    externalEvidence('ev_reply', 'Fictional author reply specifies the missing dependency; the follow-up confirms installation succeeds. No benchmark result is reported.', 'src_reply'),
  ]
  report.sources = [
    externalSource('src_metadata', 'metadata', 'metadata/reference-a', 'Fictional candidate lookup for ref_01.', now),
    externalSource('src_repo', 'repository', 'repository/synthetic-paper', 'Repository linked from the fictional paper.', now),
    externalSource('src_discussions', 'discussion_listing', 'repository/synthetic-paper/discussions', 'Fictional discussion listing for repo_01.', now),
    externalSource('src_issue', 'issue', 'repository/synthetic-paper/issues/1', 'Fictional installation discussion for repo_01.', now),
    externalSource('src_reply', 'comment', 'repository/synthetic-paper/issues/1/comment/1', 'Fictional author explanation and follow-up for discussion_01.', now),
  ]
  report.findings = [{
    finding_id: 'finding_core_contradiction', dimension_id: 'claim_evidence', item_ids: ['claim_01'],
    kind: 'concern', severity: 'critical', certainty: 'confirmed', title: 'Core claim conflicts with the result table',
    detail: 'The fictional abstract claims an improvement, while its result table reports a decrease under the same conditions. This finding remains prominent beside the 6.3 average.',
    evidence_ids: ['ev_claim', 'ev_result'], source_ids: [], limitations: ['Synthetic contradiction for UI validation; no real paper is accused.'],
  }]
  report.dimensions[0].finding_ids = ['finding_core_contradiction']
  report.alerts = [
    { type: 'low_dimension_score', dimension_id: 'claim_evidence', finding_ids: ['finding_core_contradiction'], message: 'Claim evidence scores 0/10. The other dimensions do not cancel this issue.' },
    { type: 'material_finding', dimension_id: 'claim_evidence', finding_ids: ['finding_core_contradiction'], message: 'Critical core-claim contradiction: inspect both paper locations before relying on the conclusion.' },
  ]
  return report
}

function firstCaseReport(id: string, now: string): CredibilityReport {
  const report = reportShell(id, now)
  const unassessedDimension = (dimensionId: CredibilityDimensionId): CredibilityDimension => ({
    dimension_id: dimensionId,
    applicability: dimensionId === 'claim_evidence' ? 'applicable' : 'undetermined',
    applicability_reason: dimensionId === 'claim_evidence' ? 'The prepared candidate compares one experimental claim with its reported table.' : 'Applicability and detailed scope have not been checked by this prototype.',
    check_status: dimensionId === 'claim_evidence' ? 'partial' : 'not_run',
    score_status: 'unable_to_assess',
    score: null,
    weight: 1,
    score_rule_ids: [],
    summary: dimensionId === 'claim_evidence' ? 'Prepared candidate · AI-assisted confirmation · Not Gold. One supported subclaim, with incomplete paper coverage.' : 'Not run. Empty records do not mean no concerns were found.',
    unassessed_reason: dimensionId === 'claim_evidence' ? 'Only one prepared subclaim is shown; independent double review and the new model/API run are still pending.' : 'This check has not been run.',
    evidence_ids: dimensionId === 'claim_evidence' ? ['ev_p002_claim', 'ev_p002_result'] : [],
    finding_ids: dimensionId === 'claim_evidence' ? ['finding_p002_context'] : [],
  })
  report.dimensions = [unassessedDimension('claim_evidence'), unassessedDimension('reference_verification'), unassessedDimension('code_reproduction')]
  report.checks.claims = {
    selection_scope: 'One prepared abstract subclaim from arXiv 2310.01798v1. Other paper claims have not been assessed.',
    scope_complete: false,
    scope_limitations: ['Prepared source material is replayed locally, not parsed or assessed by this prototype.', 'A reviewer adopted the AI-assisted candidate. This is not independent paper reading, double review, or Gold.'],
    items: [{
      claim_id: 'R2C-P002-C01', statement: 'Under the tested conditions, intrinsic self-correction can reduce reasoning performance.',
      importance: 'core', conditions: 'Historical GPT-4 accessed 2023-08-29; 200 sampled GSM8K questions; two self-correction rounds; no correct-label stopping feedback.',
      claim_evidence_ids: ['ev_p002_claim'], comparison_evidence_ids: ['ev_p002_result'],
      search_scope: 'Prepared abstract on physical page 1 and Table 3 on physical page 5; experiment conditions on pages 3–5.',
      outcome: 'supported', reason: 'The prepared Table 3 comparison is 95.5% initially, 91.5% after round 1, and 89.0% after round 2: a 6.5 percentage-point decrease. Table 1 uses a different, correct-label stopping condition.',
      score_rule_id: null, item_score: null,
    }],
  }
  report.checks.references = emptyReferences('Not run: this prototype has not extracted or queried the paper references.')
  report.checks.code = emptyCode('Not run: this prototype has not checked code sources or repository discussions.')
  report.evidence = [
    paperEvidence('ev_p002_claim', 'Prepared paraphrase of the abstract: reasoning self-correction without external feedback can fail to improve performance and can reduce it.', 1, 'paper_002_v1_chunk_0001'),
    paperEvidence('ev_p002_result', 'Prepared Table 3 comparison for GPT-4 / GSM8K: Standard Prompting 95.5%; Self-Correct round 1 91.5%; round 2 89.0%. No correct-label stopping feedback.', 5, 'paper_002_v1_chunk_0010', 'Table 3'),
  ]
  report.evidence[1].location_confidence = 'medium'
  report.sources = []
  report.findings = [{
    finding_id: 'finding_p002_context', dimension_id: 'claim_evidence', item_ids: ['R2C-P002-C01'], kind: 'context', severity: 'info', certainty: 'reported',
    title: 'Prepared supported candidate', detail: 'Prepared candidate · AI-assisted confirmation · Not Gold. The candidate supports only the limited historical-model comparison; it does not certify the paper.',
    evidence_ids: ['ev_p002_claim', 'ev_p002_result'], source_ids: [], limitations: ['Independent double review and actual credibility inference remain pending.', 'Do not generalize to every model, task, or self-correction method.'],
  }]
  report.overall = {
    status: 'unable_to_assess', score: null, category: null,
    summary: 'One prepared supported subclaim is available; the full three-dimension assessment has not been run.',
    unassessed_reason: 'Claim coverage is incomplete and references and code remain unchecked.',
    calculation: { method: 'equal_weight_applicable_dimensions', included_dimension_ids: ['claim_evidence'], excluded_dimension_ids: [], blocking_dimension_ids: [...dimensionIds], assessed_dimension_count: 0 },
  }
  report.alerts = dimensionIds.map((dimensionId) => ({ type: 'assessment_gap', dimension_id: dimensionId, finding_ids: dimensionId === 'claim_evidence' ? ['finding_p002_context'] : [], message: dimensionId === 'claim_evidence' ? 'Only one prepared subclaim is included; no dimension score is available.' : 'Not run. This dimension cannot contribute a score.' }))
  report.limitations.push('Real-paper identity and previously prepared source anchors are shown only as a candidate fixture. No parser or model ran in this UI.', 'Only physical pages 1 and 5 are linked as prepared evidence; experiment setup was recorded previously on pages 3–5.', 'Table cells remain a parsing limitation; page and element anchors do not by themselves prove column alignment.')
  return report
}

function externalFailureReport(id: string, now: string): CredibilityReport {
  const report = syntheticReport(id, now)
  const failed = report.sources.find((source) => source.source_id === 'src_discussions')!
  Object.assign(failed, { fetch_status: 'timeout', http_status: null, snapshot_ref: null })
  report.sources = report.sources.filter((source) => !['src_issue', 'src_reply'].includes(source.source_id))
  report.evidence = report.evidence.filter((evidence) => !['ev_discussions', 'ev_issue', 'ev_reply'].includes(evidence.evidence_id))
  report.checks.code.scope_complete = false
  report.checks.code.scope_limitations.push('The fictional discussion listing timed out, so reproduction feedback coverage is incomplete.')
  const search = report.checks.code.searches.find((item) => item.scope_type === 'discussion_list')!
  Object.assign(search, { outcome: 'failed', reason: 'Simulated timeout; no discussion listing was obtained.', evidence_ids: [] })
  report.checks.code.reproduction_signal = 'unable_to_verify'
  report.checks.code.discussions = []
  report.checks.code.base_score_rule_id = null
  report.checks.code.score_rule_id = null
  Object.assign(report.dimensions[2], {
    check_status: 'partial', score_status: 'unable_to_assess', score: null, score_rule_ids: [],
    summary: 'Fictional code files remain accessible, but reproduction discussions could not be checked.',
    unassessed_reason: 'Simulated discussion-list timeout. A failed request does not mean there is no feedback.',
    evidence_ids: ['ev_code_link', 'ev_code_files'], finding_ids: ['finding_external_gap'],
  })
  report.findings.push({
    finding_id: 'finding_external_gap', dimension_id: 'code_reproduction', item_ids: ['repo_01'], kind: 'verification_gap', severity: 'info', certainty: 'unresolved',
    title: 'Reproduction discussions could not be checked', detail: 'The fictional discussion request timed out. Existing paper findings and scored dimensions are preserved.',
    evidence_ids: [], source_ids: ['src_discussions'], limitations: ['No inference can be made about whether reproduction feedback exists.'],
  })
  report.overall = {
    status: 'unable_to_assess', score: null, category: null,
    summary: 'The confirmed fictional core contradiction and the scored reference check remain visible. A code-check gap prevents an overall score.',
    unassessed_reason: 'One applicable dimension cannot be scored because its external discussion query failed.',
    calculation: { method: 'equal_weight_applicable_dimensions', included_dimension_ids: [...dimensionIds], excluded_dimension_ids: [], blocking_dimension_ids: ['code_reproduction'], assessed_dimension_count: 2 },
  }
  report.alerts.push({ type: 'assessment_gap', dimension_id: 'code_reproduction', finding_ids: ['finding_external_gap'], message: 'External timeout: overall score and category stay blank, while confirmed findings remain.' })
  report.limitations.push('External failure is simulated; no network request was made.')
  return report
}

function notApplicableReport(id: string, now: string): CredibilityReport {
  const report = syntheticReport(id, now)
  report.checks.claims.selection_scope = 'The only theoretical abstract statement and its conditions in a fictional six-page paper.'
  report.checks.claims.items[0] = {
    claim_id: 'claim_01', statement: 'The fictional theorem applies only to finite sets under assumption A.', importance: 'core',
    conditions: 'A theoretical statement restricted to finite sets satisfying fictional assumption A.',
    claim_evidence_ids: ['ev_claim'], comparison_evidence_ids: ['ev_result'], search_scope: 'The fictional abstract, theorem statement, and its stated assumptions.',
    outcome: 'supported', reason: 'The fictional theorem statement repeats the finite-set and assumption-A restrictions. This comparison does not verify the proof.', score_rule_id: 'C_SUPPORTED', item_score: 10,
  }
  report.evidence = report.evidence.filter((evidence) => ['ev_claim', 'ev_result', 'ev_reference', 'ev_metadata'].includes(evidence.evidence_id))
  report.evidence[0] = paperEvidence('ev_claim', 'Fictional theoretical abstract: the theorem concerns finite sets satisfying assumption A; the paper has no implementation or computational experiments.', 1, 'synthetic_theory_chunk_001')
  report.evidence[1] = paperEvidence('ev_result', 'Fictional Theorem 1 is explicitly stated for finite sets satisfying assumption A.', 4, 'synthetic_theory_chunk_004', 'Theorem 1')
  report.sources = report.sources.filter((source) => source.source_id === 'src_metadata')
  report.dimensions[0] = dimension('claim_evidence', 10, 'C_SUPPORTED', 'The fictional theorem statement matches the abstract restrictions; the proof has not been verified.', ['ev_claim', 'ev_result'])
  report.dimensions[2] = {
    dimension_id: 'code_reproduction', applicability: 'not_applicable',
    applicability_reason: 'The fictional paper is purely theoretical and has no implementation or experiment dependency, as stated in its abstract.',
    check_status: 'not_run', score_status: 'not_applicable', score: null, weight: 1, score_rule_ids: [],
    summary: 'Not applicable: no program or computational experiment is required by this fictional theoretical claim.',
    unassessed_reason: null, evidence_ids: ['ev_claim'], finding_ids: [],
  }
  report.checks.code = { scope_complete: false, scope_limitations: ['Code applicability was checked; the substantive code check was not run.'], searches: [], code_availability: 'not_applicable', reproduction_signal: 'not_applicable', repositories: [], discussions: [], base_score_rule_id: null, score_rule_id: null }
  report.findings = []
  report.alerts = []
  report.overall = {
    status: 'assessed', score: 10, category: 'no_significant_concerns', summary: 'No significant concerns were found within the fictional claim and reference checks. The proof has not been verified.', unassessed_reason: null,
    calculation: { method: 'equal_weight_applicable_dimensions', included_dimension_ids: ['claim_evidence', 'reference_verification'], excluded_dimension_ids: ['code_reproduction'], blocking_dimension_ids: [], assessed_dimension_count: 2 },
  }
  report.limitations.push('Supported means the theoretical statement and its stated conditions align, not that a proof was automatically verified.')
  return report
}

function paperFor(scenario: CredibilityScenario): CredibilityPaper {
  if (scenario === 'first_case') return {
    paper_id: 'prototype_paper_002_v1', title: 'Large Language Models Cannot Self-Correct Reasoning Yet', arxiv_id: '2310.01798v1',
    source_sha256: PAPER_002_SHA, page_count: 19, language: 'en', parse_quality: 'partial',
  }
  return {
    paper_id: scenario === 'not_applicable' ? 'synthetic_theoretical_paper' : 'synthetic_paper_001',
    title: scenario === 'not_applicable' ? 'Synthetic Theoretical Paper — UI Fixture' : 'Synthetic Core-Claim Contradiction — UI Fixture',
    arxiv_id: null, source_sha256: '0'.repeat(64), page_count: 6, language: 'en', parse_quality: 'good',
  }
}

function validateRequest(request: CreateCredibilityRequest, scenario: CredibilityScenario) {
  if (request.schema_version !== CREDIBILITY_SCHEMA_VERSION || request.analysis_mode !== 'credibility' || request.rubric_version !== CREDIBILITY_RUBRIC_VERSION) {
    throw new Error('Unsupported credibility schema or rubric version. This prototype supports the independent draft only.')
  }
  if (!request.model_id.trim()) throw new Error('Choose a prototype model identifier; no model will be called.')
  if (request.source.type === 'arxiv' ? !request.source.arxiv_id.trim() || request.source.upload_id !== null : !request.source.upload_id.trim() || request.source.arxiv_id !== null) {
    throw new Error('The credibility source must contain exactly one arXiv ID or upload ID.')
  }
  if (scenario === 'first_case' && (request.source.type !== 'arxiv' || request.source.arxiv_id !== '2310.01798v1')) {
    throw new Error('The prepared first-case fixture requires arXiv 2310.01798v1. It cannot stand in for another input.')
  }
}

export const mockCredibilityApi: CredibilityApi = {
  async createAssessment(request, scenario) {
    validateRequest(request, scenario)
    const assessment: CredibilityAssessment = {
      schema_version: CREDIBILITY_SCHEMA_VERSION, analysis_mode: 'credibility', assessment_id: `cred_prototype_${++sequence}`,
      data_origin: 'synthetic', source: clone(request.source), model_id: request.model_id,
      status: 'queued', stage: 'queued', progress_percent: 0, created_at: timestamp(), started_at: null, completed_at: null,
      paper: null, report: null, warnings: [], error: null,
    }
    records.set(assessment.assessment_id, { assessment, scenario, tick: -1 })
    return clone(assessment)
  },
  async getAssessment(assessmentId, signal) {
    signal?.throwIfAborted()
    const record = records.get(assessmentId)
    if (!record) throw new Error('No local credibility fixture exists with that ID.')
    if (record.assessment.status === 'completed' || record.assessment.status === 'failed') return clone(record.assessment)
    record.tick += 1
    const step = steps[Math.min(record.tick, steps.length - 1)]
    record.assessment.started_at ??= timestamp()
    if (record.scenario === 'parse_failure' && record.tick >= 2) {
      Object.assign(record.assessment, {
        status: 'failed', stage: 'failed', progress_percent: 36, completed_at: timestamp(), paper: null, report: null,
        error: { code: 'PARSE_FAILED', message: 'Simulated parse failure: necessary text and table structure could not be recovered. No assessment or risk rating was generated.', retryable: false, debug_id: null },
      })
      return clone(record.assessment)
    }
    Object.assign(record.assessment, { status: step.stage === 'done' ? 'completed' : 'processing', stage: step.stage, progress_percent: step.progress })
    if (record.tick >= 2) record.assessment.paper = paperFor(record.scenario)
    if (step.stage === 'done') {
      const now = timestamp()
      const report = record.scenario === 'first_case' ? firstCaseReport(assessmentId, now)
        : record.scenario === 'external_failure' ? externalFailureReport(assessmentId, now)
          : record.scenario === 'not_applicable' ? notApplicableReport(assessmentId, now)
            : syntheticReport(assessmentId, now)
      report.reference_verification_summary = referenceSummary(report.checks.references)
      record.assessment.report = report
      record.assessment.completed_at = now
      record.assessment.warnings = [{ code: 'PROVISIONAL_RUBRIC', message: 'Prototype fixture with an uncalibrated provisional rubric. No parser, external fetch, or model call was made.', dimension_id: null }]
      if (record.scenario === 'first_case') record.assessment.warnings.push({ code: 'PARTIAL_PARSE', message: 'Prepared source snapshot: table-cell structure and complete claim coverage remain unresolved.', dimension_id: 'claim_evidence' })
      if (record.scenario === 'external_failure') record.assessment.warnings.push({ code: 'EXTERNAL_QUERY_FAILED', message: 'Simulated timeout in reproduction discussions; partial results were retained.', dimension_id: 'code_reproduction' })
    }
    return clone(record.assessment)
  },
  async listAssessments(search = '', offset = 0) {
    const needle = search.trim().toLowerCase()
    const items = [...records.values()].map(record => record.assessment)
      .filter(item => item.status === 'completed' || item.status === 'failed')
      .filter(item => !needle || `${item.paper?.title ?? ''} ${item.paper?.arxiv_id ?? ''}`.toLowerCase().includes(needle))
      .reverse()
      .map(item => ({
        assessment_id: item.assessment_id, status: item.status as 'completed' | 'failed', title: item.paper?.title ?? null,
        arxiv_id: item.paper?.arxiv_id ?? (item.source.type === 'arxiv' ? item.source.arxiv_id : null), source_type: item.source.type,
        model_id: item.model_id, overall_score: item.report?.overall.score ?? null, category: item.report?.overall.category ?? null,
        error_code: item.error?.code ?? null, created_at: item.created_at, completed_at: item.completed_at,
      }))
    return { items: items.slice(offset, offset + 20), total: items.length, offset, limit: 20 }
  },
}

export function resetMockCredibilityApiForTests() {
  records.clear()
  sequence = 0
}
