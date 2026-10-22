import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { AssessmentApi } from '../api/assessmentApi'
import paper002 from '../test/fixtures/credibility-paper-002.json'
import type { ReviewList } from '../types/assessment'
import type { CredibilityApi, CredibilityAssessment, CredibilityHistory } from '../types/credibility'
import { ReportsPage } from './ReportsPage'

const snapshot = window.PCC_MOCK.completed
const scoringList: ReviewList = {
  items: [{
    assessment_id: 'asmt_old', status: 'completed', title: 'Scored paper', authors: ['A. Author'], source_type: 'arxiv',
    venue: snapshot.venue, model: snapshot.model, overall_score: 7.8, rating: 'good', is_demo: false,
    created_at: '2026-10-19T01:00:00Z', completed_at: '2026-10-19T01:02:00Z',
  }],
  total: 1, offset: 0, limit: 20,
}
const credibilityList: CredibilityHistory = {
  items: [
    { assessment_id: 'cred_new', status: 'completed', title: 'Checked paper', arxiv_id: '2310.01798v1', source_type: 'arxiv', model_id: 'model_gpt_v1', overall_score: 10 / 3, category: 'significant_concerns', error_code: null, created_at: '2026-10-21T02:45:03Z', completed_at: '2026-10-21T03:08:31Z' },
    { assessment_id: 'cred_failed', status: 'failed', title: null, arxiv_id: '1706.03762', source_type: 'arxiv', model_id: 'model_gpt_v1', overall_score: null, category: null, error_code: 'MODEL_UNAVAILABLE', created_at: '2026-10-20T03:00:00Z', completed_at: '2026-10-20T03:01:00Z' },
  ],
  total: 5, offset: 0, limit: 20,
}

function apis() {
  const scoringApi = { getOptions: vi.fn(), createAssessment: vi.fn(), retryAssessment: vi.fn(),
    listAssessments: vi.fn().mockResolvedValue(scoringList), getAssessment: vi.fn().mockResolvedValue(snapshot) } as unknown as AssessmentApi
  const credibilityApi: CredibilityApi = { createAssessment: vi.fn(),
    listAssessments: vi.fn().mockResolvedValue(credibilityList), getAssessment: vi.fn().mockResolvedValue(paper002 as unknown as CredibilityAssessment) }
  return { scoringApi, credibilityApi }
}

describe('ReportsPage', () => {
  it('merges credibility checks and paper scores newest first and filters by kind', async () => {
    const { scoringApi, credibilityApi } = apis()
    render(<ReportsPage scoringApi={scoringApi} credibilityApi={credibilityApi} onOpenScoring={vi.fn()} onOpenCredibility={vi.fn()} onNew={vi.fn()} />)
    const user = userEvent.setup()
    const titles = async () => (await screen.findAllByRole('heading', { level: 2 })).map(item => item.textContent)
    expect(await titles()).toEqual(['Checked paper', '1706.03762', 'Scored paper'])
    expect(screen.getByText('3.3 / 10 · Significant concerns')).toBeInTheDocument()
    expect(screen.getByText('7.8 / 10 · Good')).toBeInTheDocument()
    expect(screen.getByText('Could not complete')).toBeInTheDocument()
    expect(screen.getByText('Stopped with MODEL_UNAVAILABLE')).toBeInTheDocument()
    expect(screen.getByText('Showing the latest 3 of 6. Search to find older reports.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Paper scores' }))
    expect(await titles()).toEqual(['Scored paper'])
  })

  it('opens each kind through its own API and searches both', async () => {
    const { scoringApi, credibilityApi } = apis()
    const onOpenScoring = vi.fn()
    const onOpenCredibility = vi.fn()
    render(<ReportsPage scoringApi={scoringApi} credibilityApi={credibilityApi} onOpenScoring={onOpenScoring} onOpenCredibility={onOpenCredibility} onNew={vi.fn()} />)
    const user = userEvent.setup()
    const cards = await screen.findAllByRole('article')
    await user.click(within(cards[0]).getByRole('button', { name: 'Open report' }))
    expect(credibilityApi.getAssessment).toHaveBeenCalledWith('cred_new', expect.any(AbortSignal))
    expect(onOpenCredibility).toHaveBeenCalledWith(paper002)
    await user.click(within(cards[2]).getByRole('button', { name: 'Open report' }))
    expect(onOpenScoring).toHaveBeenCalledWith(snapshot)
    await user.type(screen.getByLabelText('Search saved reports'), 'llm')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(credibilityApi.listAssessments).toHaveBeenLastCalledWith('llm', 0)
    expect(scoringApi.listAssessments).toHaveBeenLastCalledWith('llm', 'date', 0)
  })

  it('still lists the reports it could load when one source fails', async () => {
    const { scoringApi, credibilityApi } = apis()
    vi.mocked(credibilityApi.listAssessments!).mockRejectedValue(new Error('Credibility history is unavailable.'))
    render(<ReportsPage scoringApi={scoringApi} credibilityApi={credibilityApi} onOpenScoring={vi.fn()} onOpenCredibility={vi.fn()} onNew={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Credibility history is unavailable.')
    expect(screen.getByRole('heading', { level: 2, name: 'Scored paper' })).toBeInTheDocument()
  })
})
