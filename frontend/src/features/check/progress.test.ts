import { describe, expect, it } from 'vitest'
import type { CheckProgress } from '../../app/api'
import paper002 from '../../test/fixtures/credibility-paper-002.json'
import type { CredibilityAssessment } from '../../types/credibility'
import { describeProgress } from './progress'

const running = { ...(paper002 as unknown as CredibilityAssessment), status: 'processing', stage: 'checking', report: null, completed_at: null } as CredibilityAssessment
const now = Date.parse(running.created_at) + 10 * 60_000
const at = new Date(now - 60_000).toISOString()
const progress = (code: Partial<CheckProgress['checks']['code_check']>): CheckProgress => ({
  assessment_id: running.assessment_id, status: 'processing', stage: 'checking', progress_percent: 60, started_at: running.started_at,
  checks: {
    claim_check: { status: 'completed', finished_at: at },
    reference_check: { status: 'running', finished_at: null },
    code_check: { status: 'running', finished_at: null, ...code },
  },
})

describe('describeProgress', () => {
  it('keeps unfamiliar service step names from introducing Chinese into the progress page', () => {
    const view = describeProgress(running, progress({ last_step: '正在检查代码' }), now, null)
    expect(view.ticker.join(' ')).not.toMatch(/\p{Script=Han}/u)
    expect(view.ticker.join(' ')).toContain('processing the assessment')
  })

  it('shows a check the reader asked to stop as stopping until it ends', () => {
    const view = describeProgress(running, progress({ stop_requested: true }), now, null)
    expect(view.checks.find(check => check.id === 'code_check')).toMatchObject({ state: 'running', stopping: true, stopped: false })
    expect(view.checks.find(check => check.id === 'reference_check')).toMatchObject({ stopping: false })
  })

  it('records a stopped check as stopped by the reader, not as a finding or a plain failure', () => {
    const view = describeProgress(running, progress({ status: 'failed', finished_at: at, stopped_by_user: true }), now, null)
    expect(view.checks.find(check => check.id === 'code_check')).toMatchObject({ state: 'failed', stopped: true })
    expect(view.events.map(event => event.text)).toContain('Code check stopped by you')
    expect(view.events.map(event => event.text)).not.toContain('Code check complete (not completed)')
  })
})
