import { beforeEach, describe, expect, it } from 'vitest'
import type { ReviewSummary } from '../types/assessment'
import { pairAll } from './pairing'

const score = (assessment_id: string, title: string, created_at: string, status: ReviewSummary['status'] = 'completed') =>
  ({ assessment_id, title, created_at, status }) as ReviewSummary
const check = (assessment_id: string, title: string, created_at: string) => ({ assessment_id, title, created_at })

describe('pairAll', () => {
  beforeEach(() => localStorage.clear())

  it('pairs a run with the quality assessment started beside it, even when one parse cut the title short', () => {
    const pairs = pairAll([check('cred_a', 'Instance Normalization:', '2026-10-23T12:39:50Z')], [
      score('asmt_far', 'Instance Normalization: The Missing Ingredient for Fast Stylization', '2026-10-23T09:00:00Z'),
      score('asmt_near', 'Instance Normalization: The Missing Ingredient for Fast Stylization', '2026-10-23T12:39:54Z'),
    ])
    expect(pairs.get('cred_a')).toBe('asmt_near')
  })

  it('keeps runs of the same paper apart and still pairs a failed quality assessment', () => {
    const pairs = pairAll(
      [check('cred_first', 'Instance Normalization:', '2026-10-23T12:39:50Z'), check('cred_second', 'Instance Normalization:', '2026-10-23T12:44:00Z')],
      [score('asmt_first', 'Instance Normalization:', '2026-10-23T12:39:54Z'), score('asmt_second', 'Instance Normalization:', '2026-10-23T12:44:03Z', 'failed')],
    )
    expect([pairs.get('cred_first'), pairs.get('cred_second')]).toEqual(['asmt_first', 'asmt_second'])
  })

  it('ignores other papers, distant runs and short titles', () => {
    const pairs = pairAll(
      [check('cred_in', 'Instance Normalization:', '2026-10-23T12:39:50Z'), check('cred_bert', 'BERT', '2026-10-23T12:39:50Z')],
      [score('asmt_other', 'Layer Normalization', '2026-10-23T12:39:54Z'), score('asmt_late', 'Instance Normalization:', '2026-10-23T13:30:00Z'),
        score('asmt_bert', 'BERT: Pre-training', '2026-10-23T12:39:54Z')],
    )
    expect(pairs.size).toBe(0)
  })

  it('trusts the server link over a remembered pair and over a matching title', () => {
    localStorage.setItem('papertrace-pairs', JSON.stringify({ cred_b: 'asmt_linked' }))
    const linked = { ...score('asmt_linked', 'Instance Normalization:', '2026-10-23T12:39:54Z'), credibility_assessment_id: 'cred_a' }
    const pairs = pairAll(
      [check('cred_a', 'Instance Normalization:', '2026-10-23T12:39:50Z'), check('cred_b', 'Instance Normalization:', '2026-10-23T12:39:52Z')],
      [linked],
    )
    expect(pairs.get('cred_a')).toBe('asmt_linked')
    expect(pairs.has('cred_b')).toBe(false)
  })

  it('keeps a linked assessment for its run even when the run is not listed, and prefers the newest retry', () => {
    const pairs = pairAll([check('cred_listed', 'Instance Normalization:', '2026-10-23T12:39:50Z')], [
      { ...score('asmt_old', 'Instance Normalization:', '2026-10-23T12:40:00Z', 'failed'), credibility_assessment_id: 'cred_running' },
      { ...score('asmt_new', 'Instance Normalization:', '2026-10-23T12:45:00Z'), credibility_assessment_id: 'cred_running' },
    ])
    expect(pairs.get('cred_running')).toBe('asmt_new')
    expect(pairs.has('cred_listed')).toBe(false)
  })

  it('trusts a pair this browser remembered', () => {
    localStorage.setItem('papertrace-pairs', JSON.stringify({ cred_a: 'asmt_remembered' }))
    expect(pairAll([check('cred_a', 'Anything', '2026-10-23T12:00:00Z')], []).get('cred_a')).toBe('asmt_remembered')
  })
})
