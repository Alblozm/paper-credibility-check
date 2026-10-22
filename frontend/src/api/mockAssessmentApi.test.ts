import { beforeEach, describe, expect, it } from 'vitest'
import { mockAssessmentApi, resetMockApiForTests } from './mockAssessmentApi'

describe('mockAssessmentApi', () => {
  beforeEach(() => resetMockApiForTests())

  it('creates a queued task and advances processing status', async () => {
    const created = await mockAssessmentApi.createAssessment({
      source_type: 'arxiv',
      arxiv_id: '2501.01234',
      venue_id: 'iclr_2027_main',
      model_id: 'model_standard_v1',
    })
    expect(created.status).toBe('queued')
    expect(created.progress_percent).toBe(0)

    const processing = await mockAssessmentApi.getAssessment(created.assessment_id)
    expect(processing.status).toBe('processing')
    expect(processing.stage).toBe('validating_input')
    expect(processing.progress_percent).toBe(8)
  })

  it('returns a retryable backend-style error and can restart', async () => {
    mockAssessmentApi.setScenario('retryable_error')
    const created = await mockAssessmentApi.createAssessment({
      source_type: 'arxiv',
      arxiv_id: '2501.01234',
      venue_id: 'iclr_2027_main',
      model_id: 'model_standard_v1',
    })

    let current = await mockAssessmentApi.getAssessment(created.assessment_id)
    for (let index = 0; index < 4; index += 1) {
      current = await mockAssessmentApi.getAssessment(created.assessment_id)
    }
    expect(current.status).toBe('failed')
    expect(current.error?.retryable).toBe(true)

    const retried = await mockAssessmentApi.retryAssessment(created.assessment_id)
    expect(retried.status).toBe('queued')
    expect((await mockAssessmentApi.getAssessment(retried.assessment_id)).error).toBeNull()
  })
})
