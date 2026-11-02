import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/httpAssessmentApi'

const calls: string[] = []
const failures = new Map<string, unknown>()
const remove = (kind: string) => async (id: string) => {
  calls.push(`${kind}:${id}`)
  if (failures.has(id)) throw failures.get(id)
}
vi.mock('./api', () => ({
  credibilityApi: { deleteAssessment: remove('check') },
  scoringApi: { deleteAssessment: remove('quality') },
}))
vi.mock('./pdfStore', () => ({ deletePdf: vi.fn(async () => {}) }))

const { deleteRun } = await import('./deleteRun')
const { openTab, pairScoring, scoringFor } = await import('./jobs')

beforeEach(() => { calls.length = 0; failures.clear(); localStorage.clear() })

describe('deleteRun', () => {
  it('deletes the quality part first, then the check, and forgets the tab and pair', async () => {
    openTab('cred_1')
    pairScoring('cred_1', 'asm_1')
    await deleteRun('cred_1', null)
    expect(calls).toEqual(['quality:asm_1', 'check:cred_1'])
    expect(scoringFor('cred_1')).toBeNull()
    expect(localStorage.getItem('papertrace-tabs')).toBe('[]')
  })

  it('forgets the pair of a quality assessment deleted on its own row', async () => {
    pairScoring('cred_2', 'asm_2')
    await deleteRun(null, 'asm_2')
    expect(calls).toEqual(['quality:asm_2'])
    expect(scoringFor('cred_2')).toBeNull()
  })

  it('treats a part that is already gone as deleted', async () => {
    failures.set('asm_1', new ApiError('Assessment not found.', 404, 'NOT_FOUND'))
    await deleteRun('cred_1', 'asm_1')
    expect(calls).toEqual(['quality:asm_1', 'check:cred_1'])
  })

  it('stops before removing anything when the quality part is still running', async () => {
    failures.set('asm_1', new ApiError('The assessment is still running.', 409, 'STILL_RUNNING'))
    await expect(deleteRun('cred_1', 'asm_1')).rejects.toMatchObject({ code: 'STILL_RUNNING' })
    expect(calls).toEqual(['quality:asm_1'])
  })
})
