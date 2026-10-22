import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockCredibilityApi, resetMockCredibilityApiForTests } from './mockCredibilityApi'
import { CREDIBILITY_RUBRIC_VERSION, CREDIBILITY_SCHEMA_VERSION, type CreateCredibilityRequest, type CredibilityReport, type CredibilityScenario } from '../types/credibility'

const request: CreateCredibilityRequest = {
  schema_version: CREDIBILITY_SCHEMA_VERSION,
  analysis_mode: 'credibility',
  source: { type: 'arxiv', arxiv_id: '2310.01798v1', upload_id: null },
  model_id: 'prototype-model-not-called',
  rubric_version: CREDIBILITY_RUBRIC_VERSION,
}

beforeEach(() => resetMockCredibilityApiForTests())
afterEach(() => vi.unstubAllGlobals())

async function complete(scenario: CredibilityScenario) {
  let assessment = await mockCredibilityApi.createAssessment(request, scenario)
  for (let i = 0; i < 5 && !['completed', 'failed'].includes(assessment.status); i += 1) {
    assessment = await mockCredibilityApi.getAssessment(assessment.assessment_id)
  }
  return assessment
}

function expectValidReferences(report: CredibilityReport) {
  const evidenceIds = new Set(report.evidence.map((item) => item.evidence_id))
  const sources = new Map(report.sources.map((source) => [source.source_id, source]))
  const findingIds = new Set(report.findings.map((finding) => finding.finding_id))
  const assertEvidence = (ids: string[]) => ids.forEach((id) => expect(evidenceIds.has(id), `Unresolved evidence ${id}`).toBe(true))
  const assertSources = (ids: string[]) => ids.forEach((id) => expect(sources.has(id), `Unresolved source ${id}`).toBe(true))
  expect(evidenceIds.size).toBe(report.evidence.length)
  expect(sources.size).toBe(report.sources.length)
  expect(findingIds.size).toBe(report.findings.length)
  for (const evidence of report.evidence) {
    if (evidence.origin === 'paper') {
      expect(evidence.paper_locator).not.toBeNull()
      expect(evidence.source_id).toBeNull()
      expect(evidence.paper_locator!.page_start).toBeGreaterThan(0)
      expect(evidence.paper_locator!.page_end).toBeGreaterThanOrEqual(evidence.paper_locator!.page_start)
    } else {
      expect(evidence.paper_locator).toBeNull()
      expect(sources.get(evidence.source_id!)?.fetch_status).toBe('ok')
    }
  }
  for (const dimension of report.dimensions) {
    assertEvidence(dimension.evidence_ids)
    dimension.finding_ids.forEach((id) => expect(findingIds.has(id)).toBe(true))
    expect(dimension.check_status).not.toBe('running')
    expect(dimension.score_status).not.toBe('pending')
    if (dimension.score_status === 'scored') {
      expect(dimension.applicability).toBe('applicable')
      expect(dimension.check_status).toBe('completed')
      expect(dimension.score).not.toBeNull()
      expect(dimension.evidence_ids.length).toBeGreaterThan(0)
      expect(dimension.unassessed_reason).toBeNull()
    } else expect(dimension.score).toBeNull()
  }
  report.checks.claims.items.forEach((claim) => {
    assertEvidence(claim.claim_evidence_ids)
    assertEvidence(claim.comparison_evidence_ids)
    if (['supported', 'partially_supported', 'contradicted'].includes(claim.outcome)) expect(claim.comparison_evidence_ids.length).toBeGreaterThan(0)
  })
  const refs = report.checks.references
  expect(refs.extracted_count).toBe(refs.items.length)
  expect(refs.extracted_count).toBe(refs.checked_count + refs.not_checked_count)
  expect(refs.checked_count).toBe(refs.matched_count + refs.conflict_count + refs.unverified_count)
  refs.items.forEach((ref) => {
    assertEvidence(ref.paper_evidence_ids)
    assertSources(ref.source_ids)
    ref.candidates.forEach((candidate) => { assertEvidence(candidate.evidence_ids); assertSources([candidate.source_id]) })
    if (ref.outcome === 'matched') expect(ref.candidates.some((candidate) => candidate.candidate_id === ref.selected_candidate_id)).toBe(true)
  })
  report.checks.code.searches.forEach((search) => { assertEvidence(search.evidence_ids); assertSources(search.source_ids) })
  report.checks.code.repositories.forEach((repository) => {
    assertSources([repository.source_id])
    assertEvidence(repository.association_evidence_ids)
    assertEvidence(repository.implementation_evidence_ids)
  })
  report.checks.code.discussions.forEach((discussion) => {
    assertSources(discussion.source_ids)
    assertSources(discussion.author_response_source_ids)
    assertEvidence(discussion.evidence_ids)
    expect(report.checks.code.repositories.some((repository) => repository.repository_id === discussion.repository_id)).toBe(true)
  })
  report.findings.forEach((finding) => { assertEvidence(finding.evidence_ids); assertSources(finding.source_ids) })
  report.alerts.forEach((alert) => alert.finding_ids.forEach((id) => expect(findingIds.has(id)).toBe(true)))
}

describe('independent credibility prototype fixtures', () => {
  it('progresses through the draft task lifecycle without a network call or a premature report', async () => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    const queued = await mockCredibilityApi.createAssessment(request, 'first_case')
    expect(queued).toMatchObject({ schema_version: 'credibility-0.5.0-draft', analysis_mode: 'credibility', data_origin: 'synthetic', status: 'queued', stage: 'queued', progress_percent: 0, started_at: null, completed_at: null, paper: null, report: null, error: null })
    const stages = []
    for (let i = 0; i < 5; i += 1) {
      const snapshot = await mockCredibilityApi.getAssessment(queued.assessment_id)
      stages.push(snapshot.stage)
      expect(snapshot.data_origin).toBe('synthetic')
      expect(snapshot.started_at).not.toBeNull()
      if (i < 4) expect(snapshot).toMatchObject({ status: 'processing', report: null, completed_at: null, error: null })
      else expect(snapshot).toMatchObject({ status: 'completed', stage: 'done', progress_percent: 100, error: null })
    }
    expect(stages).toEqual(['retrieving', 'parsing', 'checking', 'finalizing', 'done'])
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('keeps the prepared first case unscored, scoped, synthetic, and separate from Gold', async () => {
    const assessment = await complete('first_case')
    expect(assessment.paper).toMatchObject({ paper_id: 'prototype_paper_002_v1', arxiv_id: '2310.01798v1', source_sha256: 'f204bfe2e642ab52abbf3fa94141e644d22a23510d6d64ed8ddc0ee9823eb983', page_count: 19 })
    const report = assessment.report!
    expect(report.checks.claims).toMatchObject({ scope_complete: false, items: [{ outcome: 'supported', item_score: null, score_rule_id: null }] })
    expect(report.evidence.map((evidence) => evidence.paper_locator?.page_start)).toEqual([1, 5])
    expect(report.evidence[1].paper_locator).toMatchObject({ chunk_id: 'paper_002_v1_chunk_0010', element_label: 'Table 3' })
    expect(report.dimensions.map((dimension) => dimension.score)).toEqual([null, null, null])
    expect(report.dimensions.slice(1).map((dimension) => dimension.check_status)).toEqual(['not_run', 'not_run'])
    expect(report.overall).toMatchObject({ status: 'unable_to_assess', score: null, category: null, calculation: { blocking_dimension_ids: ['claim_evidence', 'reference_verification', 'code_reproduction'], assessed_dimension_count: 0 } })
    expect(report.findings[0].detail).toContain('AI-assisted confirmation · Not Gold')
  })

  it('rejects other input identities for the fixed first-case fixture', async () => {
    await expect(mockCredibilityApi.createAssessment({ ...request, source: { type: 'arxiv', arxiv_id: '2310.01798v2', upload_id: null } }, 'first_case')).rejects.toThrow('requires arXiv 2310.01798v1')
    await expect(mockCredibilityApi.createAssessment({ ...request, source: { type: 'pdf', arxiv_id: null, upload_id: 'prototype_upload' } }, 'first_case')).rejects.toThrow('cannot stand in')
  })

  it('lists finished samples newest first with the backend history fields', async () => {
    const first = await complete('synthetic_complete')
    const failed = await complete('parse_failure')
    await mockCredibilityApi.createAssessment(request, 'first_case')
    const page = await mockCredibilityApi.listAssessments!()
    expect(page).toMatchObject({ total: 2, offset: 0, limit: 20 })
    expect(page.items.map(item => item.assessment_id)).toEqual([failed.assessment_id, first.assessment_id])
    expect(page.items[1]).toMatchObject({ status: 'completed', overall_score: 6.3, category: 'some_concerns', error_code: null })
    expect(page.items[0]).toMatchObject({ status: 'failed', overall_score: null, error_code: 'PARSE_FAILED' })
    expect((await mockCredibilityApi.listAssessments!('contradiction')).items).toHaveLength(1)
  })

  it('derives the reference summary from the reference check it reports', async () => {
    const summary = (await complete('synthetic_complete')).report!.reference_verification_summary
    expect(summary).toMatchObject({ extracted_count: 1, matched_count: 1, verification_rate: 1, unresolved_items: [] })
  })

  it('does not hide a critical finding behind the three-dimension synthetic average', async () => {
    const report = (await complete('synthetic_complete')).report!
    expect(report.dimensions.map((dimension) => dimension.score)).toEqual([0, 10, 9])
    expect(report.overall).toMatchObject({ score: 6.3, category: 'some_concerns' })
    expect(report.findings).toContainEqual(expect.objectContaining({ kind: 'concern', severity: 'critical', certainty: 'confirmed', evidence_ids: ['ev_claim', 'ev_result'] }))
    expect(report.alerts.map((alert) => alert.type)).toEqual(['low_dimension_score', 'material_finding'])
    expect(report.checks.code.discussions[0]).toMatchObject({ scoring_eligible: false, relevance: 'irrelevant', author_response_source_ids: ['src_reply'] })
  })

  it('preserves confirmed findings and scored dimensions when an external query times out', async () => {
    const assessment = await complete('external_failure')
    expect(assessment).toMatchObject({ status: 'completed', error: null })
    const report = assessment.report!
    expect(report.dimensions.map((dimension) => dimension.score)).toEqual([0, 10, null])
    expect(report.overall).toMatchObject({ status: 'unable_to_assess', score: null, category: null, calculation: { blocking_dimension_ids: ['code_reproduction'], assessed_dimension_count: 2 } })
    expect(report.findings).toContainEqual(expect.objectContaining({ finding_id: 'finding_core_contradiction', severity: 'critical' }))
    expect(report.sources.find((source) => source.source_id === 'src_discussions')).toMatchObject({ fetch_status: 'timeout', http_status: null, snapshot_ref: null })
    expect(report.evidence.some((evidence) => evidence.source_id === 'src_discussions')).toBe(false)
    expect(report.checks.code.reproduction_signal).toBe('unable_to_verify')
    expect(report.alerts).toContainEqual(expect.objectContaining({ type: 'assessment_gap', dimension_id: 'code_reproduction' }))
  })

  it('excludes only evidence-based code inapplicability and shows its unrun status', async () => {
    const report = (await complete('not_applicable')).report!
    expect(report.dimensions[2]).toMatchObject({ applicability: 'not_applicable', check_status: 'not_run', score_status: 'not_applicable', score: null, evidence_ids: ['ev_claim'] })
    expect(report.checks.code).toMatchObject({ scope_complete: false, searches: [], repositories: [], discussions: [], code_availability: 'not_applicable', reproduction_signal: 'not_applicable', base_score_rule_id: null, score_rule_id: null })
    expect(report.overall).toMatchObject({ score: 10, calculation: { included_dimension_ids: ['claim_evidence', 'reference_verification'], excluded_dimension_ids: ['code_reproduction'], blocking_dimension_ids: [], assessed_dimension_count: 2 } })
  })

  it('ends parsing failure without a report, rating, or forced 100% progress', async () => {
    const assessment = await complete('parse_failure')
    expect(assessment).toMatchObject({ status: 'failed', stage: 'failed', progress_percent: 36, paper: null, report: null, error: { code: 'PARSE_FAILED', retryable: false } })
    expect(assessment.completed_at).not.toBeNull()
    expect(await mockCredibilityApi.getAssessment(assessment.assessment_id)).toEqual(assessment)
  })

  it.each(['first_case', 'synthetic_complete', 'external_failure', 'not_applicable'] as const)('keeps %s evidence, source, count, and score references consistent', async (scenario) => {
    const assessment = await complete(scenario)
    const report = assessment.report!
    expect(assessment.data_origin).toBe('synthetic')
    expect(report.versions.model_calls).toEqual([])
    expect(report).toMatchObject({ rubric_status: 'provisional', is_provisional: true })
    expect(report.dimensions.map((dimension) => dimension.dimension_id)).toEqual(['claim_evidence', 'reference_verification', 'code_reproduction'])
    report.sources.forEach((source) => expect(new URL(source.url).hostname).toBe('example.invalid'))
    expectValidReferences(report)
  })

  it('does not let a caller mutate stored snapshots or advance an aborted read', async () => {
    const queued = await mockCredibilityApi.createAssessment(request, 'first_case')
    queued.source = { type: 'pdf', arxiv_id: null, upload_id: 'mutated' }
    const controller = new AbortController()
    controller.abort()
    await expect(mockCredibilityApi.getAssessment(queued.assessment_id, controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
    const retrieved = await mockCredibilityApi.getAssessment(queued.assessment_id)
    expect(retrieved.source).toEqual(request.source)
    expect(retrieved.stage).toBe('retrieving')
  })
})
