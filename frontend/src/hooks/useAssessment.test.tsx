import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAssessment } from './useAssessment'
import type { AssessmentApi } from '../api/assessmentApi'

const input = { source_type: 'arxiv' as const, arxiv_id: '2307.08122', venue_id: 'v', model_id: 'm' }
const snapshot = { ...window.PCC_MOCK.completed, status: 'processing' as const }
afterEach(() => vi.useRealTimers())

function makeApi(): AssessmentApi {
  return {
    listAssessments: vi.fn(),
    getOptions: vi.fn(), createAssessment: vi.fn().mockResolvedValue(snapshot),
    getAssessment: vi.fn().mockResolvedValue(snapshot), retryAssessment: vi.fn(),
  }
}

describe('task polling', () => {
  it('retains the task ID after first query fails and recovers automatically', async () => {
    vi.useFakeTimers()
    const api = makeApi()
    vi.mocked(api.getAssessment).mockRejectedValueOnce(new Error('offline'))
    const { result, unmount } = renderHook(() => useAssessment(api))
    await act(async () => { await result.current.submit(input) })
    expect(result.current.receipt?.assessment_id).toBe(snapshot.assessment_id)
    expect(result.current.requestError).toContain('offline')
    await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
    expect(result.current.assessment?.status).toBe('processing')
    expect(result.current.requestError).toBeNull()
    unmount()
  })

  it('ignores a delayed old response after returning to the input page', async () => {
    const api = makeApi()
    let resolve!: (value: typeof snapshot) => void
    vi.mocked(api.getAssessment).mockReturnValue(new Promise((r) => { resolve = r }))
    const { result } = renderHook(() => useAssessment(api))
    await act(async () => { await result.current.submit(input) })
    act(() => result.current.reset())
    await act(async () => { resolve(snapshot) })
    expect(result.current.assessment).toBeNull()
    expect(result.current.receipt).toBeNull()
  })
})
