import { ApiError, httpAssessmentApi, request } from '../api/httpAssessmentApi'
import { httpCredibilityApi } from '../api/httpCredibilityApi'
import { mockAssessmentApi } from '../api/mockAssessmentApi'
import { mockCredibilityApi } from '../api/mockCredibilityApi'
import type { CredibilityCheckName } from '../types/credibility'

export const isMock = import.meta.env.VITE_API_MODE === 'mock'
export const credibilityApi = isMock ? mockCredibilityApi : httpCredibilityApi
export const scoringApi = isMock ? mockAssessmentApi : httpAssessmentApi

export type CheckState = 'completed' | 'partial' | 'failed' | 'running' | 'waiting'

/** Which of the three checks have finished (GET …/progress, display only). */
export interface CheckProgress {
  assessment_id: string
  status: string
  stage: string
  progress_percent: number
  started_at: string | null
  checks: Record<CredibilityCheckName, {
    status: CheckState
    finished_at: string | null
    /** Sub-steps while the check runs: model calls by purpose, in first-seen order. */
    started_at?: string | null
    steps?: Record<string, number>
    last_step?: string | null
    /** The reader asked to stop this check; it ends within about a second. */
    stop_requested?: boolean
    /** The check ended because the reader stopped it; it carries the fallback score. */
    stopped_by_user?: boolean
  }>
}

/**
 * Null when the backend predates the progress route; the waiting page then falls back to
 * estimated progress (design spec 4.1).
 */
export async function getCheckProgress(id: string, signal?: AbortSignal): Promise<CheckProgress | null> {
  if (isMock) return null
  try {
    const value = await request<CheckProgress>(`/v1/credibility/assessments/${encodeURIComponent(id)}/progress`, { signal })
    return value?.checks ? value : null
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 405)) return null
    throw error
  }
}
