import type { AssessmentApi, AssessmentReceipt } from './assessmentApi'
import type { Assessment, CreateAssessmentRequest, ModelOption, ReviewList, VenueOption } from '../types/assessment'

export class ApiError extends Error {
  status: number
  code: string
  constructor(message: string, status = 0, code = 'NETWORK_ERROR') {
    super(message)
    this.status = status
    this.code = code
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  init.signal?.addEventListener('abort', abort, { once: true })
  if (init.signal?.aborted) controller.abort()
  const timer = window.setTimeout(abort, 30_000)
  try {
    const response = await fetch(path, { ...init, signal: controller.signal })
    const body = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = body?.detail ?? body
      const message = typeof detail?.message === 'string' ? detail.message
        : response.status === 404 ? 'The assessment or upload no longer exists. It may have been cleared when the service restarted.'
        : response.status === 422 ? 'The request did not match the API contract. Check the input and try again.'
        : `The service returned ${response.status}. Try again in a moment.`
      throw new ApiError(message, response.status, detail?.code ?? 'HTTP_ERROR')
    }
    if (!body) throw new ApiError('The service returned a response that could not be read.', response.status, 'INVALID_RESPONSE')
    return body as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (init.signal?.aborted) throw error
    throw new ApiError(controller.signal.aborted
      ? 'The request timed out. It may still have been accepted, so avoid resubmitting straight away.'
      : 'Could not reach the backend. Check that it is running and that the port is reachable.')
  } finally {
    window.clearTimeout(timer)
    init.signal?.removeEventListener('abort', abort)
  }
}

async function create(source: Assessment['source'], venue_id: string, model_id: string) {
  const receipt = await request<AssessmentReceipt>('/v1/assessments', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ schema_version: '0.4', source, venue_id, model_id }),
  })
  if (!receipt.assessment_id) throw new ApiError('The create response did not include an assessment ID.', 200, 'INVALID_RESPONSE')
  return receipt
}

export const httpAssessmentApi: AssessmentApi = {
  async listAssessments(search = '', sort = 'date', offset = 0) {
    const params = new URLSearchParams({ search, sort, offset: String(offset), limit: '20' })
    const value = await request<ReviewList>(`/v1/assessments?${params}`)
    if (!Array.isArray(value.items) || typeof value.total !== 'number') {
      throw new ApiError('The saved reviews response is incomplete.', 200, 'INVALID_RESPONSE')
    }
    return value
  },

  async getOptions() {
    const [venues, models] = await Promise.all([
      request<VenueOption[]>('/v1/venues'),
      request<ModelOption[]>('/v1/models'),
    ])
    if (!Array.isArray(venues) || !Array.isArray(models)) {
      throw new ApiError('The backend returned rubric or model options in an unexpected shape.', 200, 'INVALID_RESPONSE')
    }
    return { venues: venues.filter((v) => v.is_available), models }
  },

  async createAssessment(input: CreateAssessmentRequest) {
    if (input.source_type === 'arxiv') {
      return create({
        type: 'arxiv', arxiv_id: input.arxiv_id,
        original_url: `https://arxiv.org/abs/${input.arxiv_id}`,
        upload_id: null, original_filename: null,
      }, input.venue_id, input.model_id)
    }
    const data = new FormData()
    data.append('file', input.file)
    const upload = await request<{ upload_id: string; original_filename: string }>('/v1/uploads', {
      method: 'POST', body: data,
    })
    if (!upload.upload_id) throw new ApiError('The upload response did not include an upload ID.', 200, 'INVALID_RESPONSE')
    return create({
      type: 'pdf', arxiv_id: null, original_url: null,
      upload_id: upload.upload_id, original_filename: upload.original_filename,
    }, input.venue_id, input.model_id)
  },

  async getAssessment(id, signal) {
    const value = await request<Assessment>(`/v1/assessments/${encodeURIComponent(id)}`, { signal })
    if (value.schema_version !== '0.4' || value.assessment_id !== id
      || !value.source || !value.venue || !value.model || !Array.isArray(value.warnings)
      || !['queued', 'processing', 'completed', 'failed'].includes(value.status)
      || (value.status === 'completed' && (!value.paper || !value.report))
      || (value.status === 'failed' && !value.error)) {
      throw new ApiError('The assessment response is incomplete or uses an incompatible schema version. Check the field contract with the backend.', 200, 'INVALID_RESPONSE')
    }
    return value
  },

  async retryAssessment(id) {
    const previous = await this.getAssessment(id)
    if (previous.status !== 'failed' || !previous.error?.retryable) {
      throw new ApiError('This assessment cannot be retried as-is. Change the input and submit it again.')
    }
    return create(previous.source, previous.venue.venue_id, previous.model.model_id)
  },
}
