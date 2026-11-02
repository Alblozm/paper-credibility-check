import { ApiError } from '../api/httpAssessmentApi'
import { credibilityApi, scoringApi } from './api'
import { forgetQuality, forgetRun, scoringFor } from './jobs'
import { deletePdf } from './pdfStore'

export const canDelete = Boolean(credibilityApi.deleteAssessment && scoringApi.deleteAssessment)

/** A part that is already gone counts as deleted; the mocks report a missing record as a plain error. */
const gone = (error: unknown) => error instanceof ApiError && error.status === 404

/**
 * Deletes a run from the server: the quality assessment first, then the credibility check, so a part that is still
 * running stops the whole deletion before anything is removed. The browser then forgets the run's tab, pair and PDF.
 */
export async function deleteRun(checkId: string | null, qualityId: string | null) {
  const quality = qualityId ?? (checkId ? scoringFor(checkId) : null)
  if (quality) {
    try { await scoringApi.deleteAssessment!(quality) } catch (error) { if (!gone(error)) throw error }
    forgetQuality(quality)
  }
  if (!checkId) return
  try { await credibilityApi.deleteAssessment!(checkId) } catch (error) { if (!gone(error)) throw error }
  forgetRun(checkId)
  await deletePdf(checkId)
}
