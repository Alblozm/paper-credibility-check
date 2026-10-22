import { afterEach, describe, expect, it, vi } from 'vitest'
import { httpAssessmentApi } from './httpAssessmentApi'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
afterEach(() => vi.unstubAllGlobals())

describe('HTTP adapter', () => {
  it('keeps unavailable models visible so the UI can explain why they cannot be selected', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(json(window.PCC_MOCK.venues))
      .mockResolvedValueOnce(json([...window.PCC_MOCK.models, { model_id: 'disabled', is_available: false }])))
    const options = await httpAssessmentApi.getOptions()
    expect(options.models).toHaveLength(2)
    expect(options.models[1]).toMatchObject({ model_id: 'disabled', is_available: false })
  })

  it('uploads PDF bytes before creating the nested source request', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json({ upload_id: 'upl_test', original_filename: 'paper.pdf' }))
      .mockResolvedValueOnce(json({ assessment_id: 'asmt_new' }, 202))
    vi.stubGlobal('fetch', fetcher)
    const file = new File(['%PDF-test'], 'paper.pdf', { type: 'application/pdf' })
    const receipt = await httpAssessmentApi.createAssessment({ source_type: 'pdf', file, venue_id: 'v', model_id: 'm' })
    expect(receipt.assessment_id).toBe('asmt_new')
    const [url, upload] = fetcher.mock.calls[0]
    expect(url).toBe('/v1/uploads')
    expect(upload.body.get('file')).toBe(file)
    expect(upload.headers).toBeUndefined()
    const body = JSON.parse(fetcher.mock.calls[1][1].body)
    expect(body.source).toEqual({ type: 'pdf', upload_id: 'upl_test', original_filename: 'paper.pdf', arxiv_id: null, original_url: null })
    expect(body.schema_version).toBe('0.4')
  })

  it('maps arXiv input and returns a receipt without pretending it is a report', async () => {
    const fetcher = vi.fn().mockResolvedValue(json({ assessment_id: 'asmt_new' }, 202))
    vi.stubGlobal('fetch', fetcher)
    await httpAssessmentApi.createAssessment({ source_type: 'arxiv', arxiv_id: '2307.08122v3', venue_id: 'v', model_id: 'm' })
    expect(JSON.parse(fetcher.mock.calls[0][1].body).source.arxiv_id).toBe('2307.08122v3')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('rejects incomplete task snapshots instead of falling back to mock', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ assessment_id: 'a', status: 'completed' })))
    await expect(httpAssessmentApi.getAssessment('a')).rejects.toThrow('incomplete')
  })

  it('surfaces backend upload errors without creating a task', async () => {
    const fetcher = vi.fn().mockResolvedValue(json({ detail: { code: 'INVALID_PDF', message: 'PDF 已损坏。' /* backend-authored text, surfaced verbatim */ } }, 400))
    vi.stubGlobal('fetch', fetcher)
    await expect(httpAssessmentApi.createAssessment({ source_type: 'pdf', file: new File(['bad'], 'p.pdf'), venue_id: 'v', model_id: 'm' })).rejects.toThrow('PDF 已损坏')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('retries a retryable failure by creating a new task with the original source', async () => {
    const previous = { ...window.PCC_MOCK.completed, status: 'failed', error: window.PCC_MOCK.errors.retryable }
    const fetcher = vi.fn().mockResolvedValueOnce(json(previous)).mockResolvedValueOnce(json({ assessment_id: 'asmt_retry' }, 202))
    vi.stubGlobal('fetch', fetcher)
    expect((await httpAssessmentApi.retryAssessment(previous.assessment_id)).assessment_id).toBe('asmt_retry')
    expect(JSON.parse(fetcher.mock.calls[1][1].body).source).toEqual(previous.source)
  })
})
