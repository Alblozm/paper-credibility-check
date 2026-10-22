import { afterEach, describe, expect, it, vi } from 'vitest'
import paper002 from '../test/fixtures/credibility-paper-002.json'
import type { ModelOption } from '../types/assessment'
import type { CreateCredibilityRequest, CredibilityAssessment } from '../types/credibility'
import { credibilityModelChoice, httpCredibilityApi } from './httpCredibilityApi'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const completed = paper002 as unknown as CredibilityAssessment
const request: CreateCredibilityRequest = {
  schema_version: 'credibility-0.5.0-draft', analysis_mode: 'credibility',
  source: { type: 'arxiv', arxiv_id: '2310.01798v1', upload_id: null }, model_id: 'model_standard_v1', rubric_version: '0.2-provisional-complete',
}
const model = (model_id: string, provider: string, extra: Partial<ModelOption> = {}): ModelOption => ({
  model_id, provider, display_name: model_id, model_version: 'v', description: '', speed_tier: 'standard', is_available: true, is_default: false, ...extra,
})
afterEach(() => vi.unstubAllGlobals())

describe('HTTP credibility adapter', () => {
  it('posts the independent credibility request and accepts the queued snapshot', async () => {
    const queued = { ...completed, status: 'queued', stage: 'queued', progress_percent: 0, report: null, paper: null, warnings: [] }
    const fetcher = vi.fn().mockResolvedValue(json(queued, 202))
    vi.stubGlobal('fetch', fetcher)
    const value = await httpCredibilityApi.createAssessment(request, 'first_case')
    expect(value.status).toBe('queued')
    const [url, init] = fetcher.mock.calls[0]
    expect(url).toBe('/v1/credibility/assessments')
    expect(JSON.parse(init.body)).toEqual(request)
  })

  it('accepts a real backend v1.0 report for paper_002 with a trial score', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(completed)))
    const value = await httpCredibilityApi.getAssessment(completed.assessment_id)
    expect(value.report?.checks.claims.items[0].outcome).toBe('unable_to_determine')
    expect(value.report?.overall).toMatchObject({ category: 'significant_concerns', calculation: { method: 'equal_weight_trial_dimensions', fallback_dimension_ids: ['claim_evidence', 'reference_verification'] } })
    expect(value.report?.execution.checks?.reference_check).toMatchObject({ status: 'failed', error: { code: 'TimeoutError' } })
  })

  it('rejects five-dimension or incomplete snapshots', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ ...completed, schema_version: '0.4' })))
    await expect(httpCredibilityApi.getAssessment(completed.assessment_id)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ ...completed, report: null })))
    await expect(httpCredibilityApi.getAssessment(completed.assessment_id)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it('rejects a 0.5 snapshot that lacks the reference summary or code discovery', async () => {
    const { reference_verification_summary: _, ...report } = completed.report!
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ ...completed, report })))
    await expect(httpCredibilityApi.getAssessment(completed.assessment_id)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it('lists saved credibility checks with the same paging shape as five-dimension history', async () => {
    const page = { items: [{ assessment_id: 'cred_1', status: 'completed', title: 'T', arxiv_id: '1706.03762', source_type: 'arxiv', model_id: 'model_gpt_v1', overall_score: 8, category: 'no_significant_concerns', error_code: null, created_at: 'c', completed_at: 'd' }], total: 1, offset: 0, limit: 20 }
    const fetcher = vi.fn().mockResolvedValue(json(page))
    vi.stubGlobal('fetch', fetcher)
    expect(await httpCredibilityApi.listAssessments!('attention', 20)).toEqual(page)
    expect(fetcher.mock.calls[0][0]).toBe('/v1/credibility/assessments?search=attention&offset=20&limit=20')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ items: null })))
    await expect(httpCredibilityApi.listAssessments!()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it('offers only real models, and falls back to an offline sample on a fixture backend', () => {
    const gpt = model('model_gpt_v1', 'openai', { is_default: true })
    const deepseek = model('model_standard_v1', 'deepseek')
    expect(credibilityModelChoice([deepseek, gpt])).toEqual({ models: [deepseek, gpt], defaultId: 'model_gpt_v1', offline: false })
    expect(credibilityModelChoice([deepseek, { ...gpt, is_available: false }]).defaultId).toBe('model_standard_v1')
    const fixture = model('model_fixture_v1', 'internal', { is_default: true })
    expect(credibilityModelChoice([{ ...deepseek, is_available: false }, fixture])).toEqual({ models: [], defaultId: 'not_called', offline: true })
    expect(credibilityModelChoice([{ ...deepseek, is_available: false }]).defaultId).toBeNull()
  })

  it('uploads a PDF through the shared upload endpoint', async () => {
    const fetcher = vi.fn().mockResolvedValue(json({ upload_id: 'upl_0123456789ab' }, 201))
    vi.stubGlobal('fetch', fetcher)
    const file = new File(['%PDF-test'], 'paper.pdf', { type: 'application/pdf' })
    expect(await httpCredibilityApi.uploadPdf!(file)).toBe('upl_0123456789ab')
    expect(fetcher.mock.calls[0][0]).toBe('/v1/uploads')
    expect(fetcher.mock.calls[0][1].body.get('file')).toBe(file)
  })
})
