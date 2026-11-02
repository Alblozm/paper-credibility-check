import { ApiError, request } from './httpAssessmentApi'
import type { ModelOption } from '../types/assessment'
import { withEnglishMessages } from '../utils/englishCopy'
import {
  CREDIBILITY_SCHEMA_VERSION,
  type CredibilityApi,
  type CredibilityAssessment,
  type CredibilityHistory,
} from '../types/credibility'

function checked(value: CredibilityAssessment, id?: string) {
  if (value?.schema_version !== CREDIBILITY_SCHEMA_VERSION || value.analysis_mode !== 'credibility'
    || (id !== undefined && value.assessment_id !== id) || !value.assessment_id
    || !value.source || !Array.isArray(value.warnings)
    || !['queued', 'processing', 'completed', 'failed'].includes(value.status)
    || (value.status === 'completed' && (!value.report || value.report.dimensions?.length !== 3
      || !value.report.reference_verification_summary || !value.report.code_discovery))
    || (value.status === 'failed' && !value.error)) {
    throw new ApiError('The credibility response is incomplete or uses an incompatible contract version.', 200, 'INVALID_RESPONSE')
  }
  return withEnglishMessages(value)
}

/**
 * Any available real model can run a check; the backend decides which providers it supports. A fixture
 * backend lists a demo model instead, and then only 'not_called' runs: an offline sample, never a real check.
 * Listed models that are not available (not yet verified, or missing a key) come back as `pending`, so the
 * picker can show them disabled.
 */
export function credibilityModelChoice(models: ModelOption[]) {
  const live = models.filter(item => item.is_available && item.provider !== 'internal')
  const pending = models.filter(item => item.provider !== 'internal' && !live.includes(item))
  const offline = !live.length && models.some(item => item.is_available && item.provider === 'internal')
  const defaultId = (live.find(item => item.is_default) ?? live[0])?.model_id ?? (offline ? 'not_called' : null)
  return { models: live, pending, defaultId, offline }
}

export const httpCredibilityApi: CredibilityApi = {
  async createAssessment(body) {
    return checked(await request<CredibilityAssessment>('/v1/credibility/assessments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }))
  },

  async getAssessment(id, signal) {
    return checked(await request<CredibilityAssessment>(`/v1/credibility/assessments/${encodeURIComponent(id)}`, { signal }), id)
  },

  async stopCheck(id, check) {
    await request<unknown>(`/v1/credibility/assessments/${encodeURIComponent(id)}/checks/${check}/stop`, { method: 'POST' })
  },

  async deleteAssessment(id) {
    await request<unknown>(`/v1/credibility/assessments/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },

  async cancelAssessment(id) {
    return checked(await request<CredibilityAssessment>(`/v1/credibility/assessments/${encodeURIComponent(id)}/cancel`, { method: 'POST' }), id)
  },

  async uploadPdf(file) {
    const data = new FormData()
    data.append('file', file)
    const upload = await request<{ upload_id: string }>('/v1/uploads', { method: 'POST', body: data })
    if (!upload.upload_id) throw new ApiError('The upload response did not include an upload ID.', 200, 'INVALID_RESPONSE')
    return upload.upload_id
  },

  async listAssessments(search = '', offset = 0) {
    const params = new URLSearchParams({ search, offset: String(offset), limit: '20' })
    const value = await request<CredibilityHistory>(`/v1/credibility/assessments?${params}`)
    if (!Array.isArray(value.items) || typeof value.total !== 'number') {
      throw new ApiError('The saved credibility checks response is incomplete.', 200, 'INVALID_RESPONSE')
    }
    return value
  },

  async getModels() {
    const models = await request<ModelOption[]>('/v1/models')
    if (!Array.isArray(models)) throw new ApiError('The backend returned model options in an unexpected shape.', 200, 'INVALID_RESPONSE')
    return models
  },
}
