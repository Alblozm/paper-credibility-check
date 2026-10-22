import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/httpAssessmentApi'
import { mockCredibilityApi, resetMockCredibilityApiForTests } from '../api/mockCredibilityApi'
import paper001Code from '../test/fixtures/credibility-paper-001-code.json'
import paper002 from '../test/fixtures/credibility-paper-002.json'
import scoring002 from '../test/fixtures/scoring-paper-002.json'
import type { AssessmentApi } from '../api/assessmentApi'
import type { Assessment, ModelOption } from '../types/assessment'
import type { CredibilityApi, CredibilityAssessment } from '../types/credibility'
import { CredibilityWorkspace } from './CredibilityWorkspace'

beforeEach(() => resetMockCredibilityApiForTests())

const deepseek: ModelOption = { model_id: 'model_standard_v1', display_name: 'DeepSeek Review Model', provider: 'deepseek', model_version: 'deepseek-v4-flash', description: '', speed_tier: 'standard', is_available: true, is_default: true }
const gpt: ModelOption = { ...deepseek, model_id: 'model_gpt_v1', display_name: 'GPT Review Model', provider: 'openai', is_default: false }

function workspace() {
  render(<CredibilityWorkspace pollingDelay={1} />)
  return userEvent.setup()
}

async function preview(user: ReturnType<typeof userEvent.setup>, scenario?: string) {
  if (scenario) await user.selectOptions(screen.getByLabelText('Preview scenario'), scenario)
  await user.click(screen.getByRole('button', { name: 'Preview check' }))
  if (scenario === 'parse_failure') await screen.findByRole('button', { name: 'Back to input' })
  else await screen.findByRole('region', { name: 'Overall assessment' })
}

describe('CredibilityWorkspace', () => {
  it('previews the prepared candidate with null overall and both evidence ends, without a network request', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
    const user = workspace()
    expect(screen.queryByText('ICLR')).not.toBeInTheDocument()
    await preview(user)
    expect(within(screen.getByRole('region', { name: 'Overall assessment' })).getByText('—')).toBeInTheDocument()
    expect(screen.getByText('Prepared candidate · AI-assisted confirmation · Not Gold')).toBeInTheDocument()
    expect(screen.getByText('Supported')).toBeInTheDocument()
    const claim = screen.getByLabelText('Claim evidence')
    await user.click(within(claim).getByRole('button'))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('link')).toHaveAttribute('href', 'https://arxiv.org/pdf/2310.01798v1#page=1')
    await user.keyboard('{Escape}')
    await user.click(within(screen.getByLabelText('Comparison evidence')).getAllByRole('button')[0])
    expect(within(screen.getByRole('dialog')).getByRole('link')).toHaveAttribute('href', 'https://arxiv.org/pdf/2310.01798v1#page=5')
    expect(fetch).not.toHaveBeenCalled()
    fetch.mockRestore()
  })

  it('retains material and low-score alerts beside a nonzero aggregate', async () => {
    const user = workspace()
    await preview(user, 'synthetic_complete')
    expect(within(screen.getByRole('region', { name: 'Overall assessment' })).getByText('6.3')).toBeInTheDocument()
    expect(screen.getByText('Some concerns')).toBeInTheDocument()
    expect(screen.getByText('Important finding')).toBeInTheDocument()
    expect(screen.getByText('Low check score')).toBeInTheDocument()
    expect(screen.getByText('Contradicted')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /References/ }))
    expect(screen.getByText('Matched', { selector: '.cred-counts span' }).previousSibling).toHaveTextContent('1')
    expect(screen.getByText('Metadata conflict', { selector: '.cred-counts span' }).previousSibling).toHaveTextContent('0')
    expect(screen.getByText('Query completion')).toBeInTheDocument()
    expect(screen.getByText('Extraction coverage')).toBeInTheDocument()
  })

  it('keeps other results after external failure while leaving the overall score empty', async () => {
    const user = workspace()
    await preview(user, 'external_failure')
    expect(within(screen.getByRole('region', { name: 'Overall assessment' })).getByText('—')).toBeInTheDocument()
    expect(screen.getByText('Important finding')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /Code & reproduction/ }))
    const source = screen.getAllByText(/Source record · discussion listing/)[0]
    await user.click(source)
    const record = within(source.closest('details')!)
    expect(record.getByText('timeout')).toBeInTheDocument()
    expect(record.getByText('Observed')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /example/ })).not.toBeInTheDocument()
  })

  it('shows code exclusion separately from an unscored applicable dimension', async () => {
    const user = workspace()
    await preview(user, 'not_applicable')
    const code = screen.getByRole('tab', { name: /Code & reproduction/ })
    expect(code).toHaveTextContent('Not applicable')
    await user.click(code)
    await user.click(screen.getByText('Applicability & scoring reason'))
    expect(screen.getByText('The fictional paper is purely theoretical and has no implementation or experiment dependency, as stated in its abstract.')).toBeInTheDocument()
    await user.click(screen.getByText('Coverage, limitations & report provenance'))
    expect(screen.getByText('Excluded as not applicable')).toBeInTheDocument()
  })

  it('returns a parse failure without inventing a score or risk category', async () => {
    const user = workspace()
    await preview(user, 'parse_failure')
    expect(screen.getByText('No credibility score or risk category was produced.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Overall assessment' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Back to input' })).toBeInTheDocument()
  })

  it('rejects a different paper for the locked first case', async () => {
    const user = workspace()
    await user.clear(screen.getByLabelText('arXiv ID or link'))
    await user.type(screen.getByLabelText('arXiv ID or link'), '1706.03762')
    await user.click(screen.getByRole('button', { name: 'Preview check' }))
    expect(screen.getByRole('alert')).toHaveTextContent('locked to 2310.01798v1')
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('renders a live trial-scored report and traces both claim ends to their PDF pages', async () => {
    const completed = paper002 as unknown as CredibilityAssessment
    const queued = { ...completed, status: 'queued', stage: 'queued', progress_percent: 0, report: null, paper: null } as CredibilityAssessment
    const api: CredibilityApi = { createAssessment: vi.fn().mockResolvedValue(queued), getAssessment: vi.fn().mockResolvedValue(completed), getModels: vi.fn().mockResolvedValue([deepseek]) }
    render(<CredibilityWorkspace pollingDelay={1} api={api} live />)
    const user = userEvent.setup()
    expect(screen.queryByLabelText('Preview scenario')).not.toBeInTheDocument()
    await screen.findByText('DeepSeek Review Model')
    await user.click(screen.getByRole('button', { name: 'Run check' }))
    await screen.findByRole('region', { name: 'Overall assessment' })
    expect(vi.mocked(api.createAssessment).mock.calls[0][0].source).toEqual({ type: 'arxiv', arxiv_id: '2310.01798v1', upload_id: null })
    expect(screen.getByText('Live check · provisional')).toBeInTheDocument()
    const overall = within(screen.getByRole('region', { name: 'Overall assessment' }))
    expect(overall.getByText('3.3')).toBeInTheDocument()
    expect(overall.getByText('Significant concerns')).toBeInTheDocument()
    expect(overall.getByText(/trial score/)).toBeInTheDocument()
    expect(overall.getByText('Fallback scores for incomplete checks: Claims & evidence, References.')).toBeInTheDocument()
    expect(screen.getAllByText('Unable to determine').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Supported').length).toBeGreaterThan(0)
    expect(screen.getByText(/has not been human-reviewed/)).toBeInTheDocument()
    await user.click(within(screen.getAllByLabelText('Claim evidence')[0]).getAllByRole('button')[0])
    expect(within(screen.getByRole('dialog')).getByRole('link')).toHaveAttribute('href', 'https://arxiv.org/pdf/2310.01798v1#page=1')
    await user.keyboard('{Escape}')
    await user.click(within(screen.getAllByLabelText('Comparison evidence')[0]).getByRole('button'))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Discussion')).toBeInTheDocument()
    expect(within(dialog).getByRole('link')).toHaveAttribute('href', 'https://arxiv.org/pdf/2310.01798v1#page=8')
  })

  it('names the confirmed repository and keeps an undetermined discussion out of the score', async () => {
    const completed = paper001Code as unknown as CredibilityAssessment
    const api: CredibilityApi = { createAssessment: vi.fn().mockResolvedValue(completed), getAssessment: vi.fn(), getModels: vi.fn().mockResolvedValue([deepseek]) }
    render(<CredibilityWorkspace pollingDelay={1} api={api} live />)
    const user = userEvent.setup()
    await screen.findByText('DeepSeek Review Model')
    await user.click(screen.getByRole('button', { name: 'Run check' }))
    await screen.findByRole('region', { name: 'Overall assessment' })
    await user.click(screen.getByRole('tab', { name: /Code & reproduction/ }))
    const panel = within(screen.getByRole('tabpanel'))
    expect(panel.getByText('Code available')).toBeInTheDocument()
    expect(panel.getByRole('link', { name: 'tianyu139/tangent-model-composition' }))
      .toHaveAttribute('href', 'https://github.com/tianyu139/tangent-model-composition')
    expect(panel.getByText('1f282134c7d5')).toBeInTheDocument()
    expect(panel.getByText('Discussion only · relevance undetermined')).toBeInTheDocument()
    expect(panel.getByText('Counts toward score').nextSibling).toHaveTextContent('No')
  })

  it('submits the 0.5 contract with the chosen model, or an offline sample on a fixture backend', async () => {
    const completed = paper002 as unknown as CredibilityAssessment
    const api: CredibilityApi = { createAssessment: vi.fn().mockResolvedValue(completed), getAssessment: vi.fn(), getModels: vi.fn().mockResolvedValue([deepseek, gpt]) }
    const { unmount } = render(<CredibilityWorkspace pollingDelay={1} api={api} live />)
    const user = userEvent.setup()
    await user.selectOptions(await screen.findByLabelText('Model'), 'model_gpt_v1')
    await user.click(screen.getByRole('button', { name: 'Run check' }))
    await screen.findByRole('region', { name: 'Overall assessment' })
    expect(vi.mocked(api.createAssessment).mock.calls[0][0]).toMatchObject({ schema_version: 'credibility-0.5.0-draft', rubric_version: '0.2-provisional-complete', model_id: 'model_gpt_v1' })
    unmount()

    const fixture = { ...deepseek, model_id: 'model_fixture_v1', provider: 'internal' }
    const offline: CredibilityApi = { ...api, createAssessment: vi.fn().mockResolvedValue(completed), getModels: vi.fn().mockResolvedValue([fixture]) }
    render(<CredibilityWorkspace pollingDelay={1} api={offline} live />)
    await screen.findByText(/Fixture backend · no model call/)
    await user.click(screen.getByRole('button', { name: 'Run check' }))
    await screen.findByRole('region', { name: 'Overall assessment' })
    expect(vi.mocked(offline.createAssessment).mock.calls[0][0].model_id).toBe('not_called')
  })

  it('blocks a live check when the backend has no usable model', async () => {
    const api: CredibilityApi = { createAssessment: vi.fn(), getAssessment: vi.fn(), getModels: vi.fn().mockResolvedValue([{ ...deepseek, is_available: false }]) }
    render(<CredibilityWorkspace pollingDelay={1} api={api} live />)
    expect(await screen.findByRole('alert')).toHaveTextContent('No model is available for live checks')
    expect(screen.getByRole('button', { name: 'Run check' })).toBeDisabled()
    expect(api.createAssessment).not.toHaveBeenCalled()
  })

  it('cancels a late create response without reopening the report', async () => {
    const original = mockCredibilityApi.createAssessment
    let finish: (() => void) | undefined
    const create = vi.spyOn(mockCredibilityApi, 'createAssessment').mockImplementation(async (...args) => {
      await new Promise<void>(resolve => { finish = resolve })
      return original(...args)
    })
    const user = workspace()
    await user.click(screen.getByRole('button', { name: 'Preview check' }))
    expect(screen.getByLabelText('Preview scenario')).toBeDisabled()
    expect(screen.getByLabelText('arXiv ID or link')).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    finish?.()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Preview check' })).toBeEnabled())
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    create.mockRestore()
  })

  it('lets the reader open an example report from the check home and close it again', async () => {
    const example = paper002 as unknown as CredibilityAssessment
    const api: CredibilityApi = {
      createAssessment: vi.fn(), getAssessment: vi.fn().mockResolvedValue(example), getModels: vi.fn().mockResolvedValue([deepseek]),
      listAssessments: vi.fn().mockResolvedValue({ items: [
        { assessment_id: example.assessment_id, status: 'completed', title: example.paper!.title, arxiv_id: '2310.01798v1', source_type: 'arxiv', model_id: 'model_standard_v1', overall_score: 10 / 3, category: 'significant_concerns', error_code: null, created_at: example.created_at, completed_at: example.completed_at },
        { assessment_id: 'cred_failed', status: 'failed', title: null, arxiv_id: '1706.03762', source_type: 'arxiv', model_id: 'model_standard_v1', overall_score: null, category: null, error_code: 'MODEL_UNAVAILABLE', created_at: example.created_at, completed_at: example.completed_at },
      ], total: 2, offset: 0, limit: 20 }),
    }
    render(<CredibilityWorkspace pollingDelay={1} api={api} live />)
    const user = userEvent.setup()
    const examples = within(await screen.findByRole('region', { name: 'Example reports' }))
    expect(examples.getAllByRole('button')).toHaveLength(1)
    await user.click(examples.getByRole('button', { name: /Large Language Models Cannot Self-Correct/ }))
    expect(await screen.findByText(/Saved report · opened without running anything/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Large Language Models Cannot Self-Correct Reasoning Yet' })).toBeInTheDocument()
    expect(api.createAssessment).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.getByRole('button', { name: 'Run check' })).toBeInTheDocument()
  })

  it('keeps a running check going while the quick paper score and examples are read', async () => {
    const completed = paper002 as unknown as CredibilityAssessment
    const processing = { ...completed, status: 'processing', stage: 'checking', progress_percent: 60, report: null } as CredibilityAssessment
    const api: CredibilityApi = {
      createAssessment: vi.fn().mockResolvedValue({ ...processing, status: 'queued', stage: 'queued', progress_percent: 0 }),
      getAssessment: vi.fn().mockResolvedValue(processing), getModels: vi.fn().mockResolvedValue([deepseek]),
      listAssessments: vi.fn().mockResolvedValue({ items: [], total: 0, offset: 0, limit: 20 }),
    }
    const scored = scoring002 as unknown as Assessment
    const scoringApi = {
      getOptions: vi.fn().mockResolvedValue({ venues: window.PCC_MOCK.venues, models: [{ ...deepseek, is_default: true }] }),
      createAssessment: vi.fn().mockResolvedValue({ assessment_id: scored.assessment_id, status: 'queued', stage: 'queued', progress_percent: 0, schema_version: '0.4', created_at: scored.created_at }),
      getAssessment: vi.fn().mockResolvedValue(scored), listAssessments: vi.fn(), retryAssessment: vi.fn(),
    } as unknown as AssessmentApi
    const onOpenScoring = vi.fn()
    const onRunningChange = vi.fn()
    const { rerender } = render(<CredibilityWorkspace pollingDelay={1} api={api} scoringApi={scoringApi} live onOpenScoring={onOpenScoring} onRunningChange={onRunningChange} />)
    const user = userEvent.setup()
    await screen.findByText('DeepSeek Review Model')
    expect(screen.getByLabelText(/Also score the paper on five dimensions/)).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Run check' }))
    const side = within(await screen.findByRole('complementary', { name: 'While you wait' }))
    await user.click(await side.findByRole('button', { name: 'Read the scoring report →' }))
    expect(onOpenScoring).toHaveBeenCalledWith(scored)
    expect(vi.mocked(scoringApi.createAssessment).mock.calls[0][0]).toMatchObject({ source_type: 'arxiv', arxiv_id: '2310.01798v1', model_id: 'model_standard_v1' })
    expect(side.getByText('7.0 / 10')).toBeInTheDocument()
    expect(onRunningChange).toHaveBeenLastCalledWith(expect.objectContaining({ stage: 'Compare checks', progress: 60 }))
    rerender(<CredibilityWorkspace pollingDelay={1} api={api} scoringApi={scoringApi} live onOpenScoring={onOpenScoring} onRunningChange={onRunningChange} homeRequest={1} />)
    expect(screen.getByRole('progressbar', { name: 'Check progress' })).toBeInTheDocument()
    expect(vi.mocked(api.getAssessment).mock.calls.length).toBeGreaterThan(1)
  })

  it('rides out a dropped status request and lets the reader resume after a permanent one', async () => {
    const completed = paper002 as unknown as CredibilityAssessment
    const processing = { ...completed, status: 'processing', stage: 'checking', progress_percent: 60, report: null } as CredibilityAssessment
    const getAssessment = vi.fn()
      .mockRejectedValueOnce(new ApiError('Could not reach the backend.'))
      .mockResolvedValueOnce(processing)
      .mockRejectedValueOnce(new ApiError('The service returned 404.', 404, 'ASSESSMENT_NOT_FOUND'))
      .mockResolvedValueOnce(completed)
    const api: CredibilityApi = { createAssessment: vi.fn().mockResolvedValue(processing), getAssessment, getModels: vi.fn().mockResolvedValue([deepseek]) }
    render(<CredibilityWorkspace pollingDelay={1} api={api} live />)
    const user = userEvent.setup()
    await screen.findByText('DeepSeek Review Model')
    await user.click(screen.getByRole('button', { name: 'Run check' }))
    expect(await screen.findByText('The service returned 404.')).toHaveAttribute('role', 'alert')
    expect(getAssessment).toHaveBeenCalledTimes(3)
    await user.click(screen.getByRole('button', { name: 'Resume polling' }))
    await screen.findByRole('region', { name: 'Overall assessment' })
    expect(getAssessment).toHaveBeenCalledTimes(4)
  })
})
