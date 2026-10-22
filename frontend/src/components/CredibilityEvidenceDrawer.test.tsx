import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { CredibilityEvidence, CredibilityExternalSource } from '../types/credibility'
import { CredibilityEvidenceDrawer } from './CredibilityEvidenceDrawer'

const paper: CredibilityEvidence = {
  evidence_id: 'ev_claim',
  origin: 'paper',
  mode: 'quote',
  excerpt: 'Their performance might degrade after self-correction.',
  paper_locator: {
    page_start: 1, page_end: 1, chunk_id: 'paper_002_v1_chunk_0001',
    section_title: 'Abstract', element_label: null,
  },
  source_id: null,
  location_confidence: 'high',
}
const comparison: CredibilityEvidence = {
  ...paper,
  evidence_id: 'ev_result',
  excerpt: 'GPT-4 / GSM8K: 95.5% before, 89.0% after round 2.',
  paper_locator: {
    page_start: 4, page_end: 5, chunk_id: 'paper_002_v1_chunk_0010',
    section_title: 'Intrinsic self-correction', element_label: 'Table 3',
  },
  location_confidence: 'medium',
}
const external: CredibilityEvidence = {
  evidence_id: 'ev_external', origin: 'external', mode: 'paraphrase',
  excerpt: 'A discussion reports a reproduction question.', paper_locator: null,
  source_id: 'src_issue', location_confidence: 'high',
}
const source: CredibilityExternalSource = {
  source_id: 'src_issue', kind: 'issue', url: 'https://github.com/team/repo/issues/3',
  fetch_status: 'ok', http_status: 200,
  observed_at: '2026-09-29T02:00:00Z', retrieved_at: '2026-10-01T04:00:00Z',
  is_cached: true, snapshot_ref: 'snapshot:issue-3',
  association_reason: 'This discussion belongs to the repository explicitly linked by the paper.',
}
const base = {
  evidence: [paper, comparison, external], sources: [source], selectedId: 'ev_claim',
  paperPdfUrl: 'https://arxiv.org/pdf/2310.01798v1', onSelect: vi.fn(), onClose: vi.fn(),
}

describe('credibility evidence drawer', () => {
  it('shows the PDF page range, table label, excerpt and real original link', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<CredibilityEvidenceDrawer {...base} selectedId="ev_result" onSelect={onSelect} />)

    expect(screen.getByRole('dialog', { name: 'Paper passage' })).toBeInTheDocument()
    expect(screen.getByText('Pages 4–5')).toBeInTheDocument()
    expect(screen.getByText('Table 3')).toBeInTheDocument()
    expect(screen.getByText(comparison.excerpt)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open PDF at page 4' }))
      .toHaveAttribute('href', 'https://arxiv.org/pdf/2310.01798v1#page=4')
    expect(screen.getByText('Verify this location against the source before quoting.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Previous evidence' }))
    expect(onSelect).toHaveBeenLastCalledWith('ev_claim')
    await user.click(screen.getByRole('button', { name: 'Next evidence' }))
    expect(onSelect).toHaveBeenLastCalledWith('ev_external')
  })

  it('keeps keyboard focus inside, closes with Escape, and restores the trigger focus', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    const content = (selectedId: string | null) => <>
      <button type="button">Open evidence</button>
      <CredibilityEvidenceDrawer {...base} selectedId={selectedId} onClose={onClose} />
    </>
    const { rerender } = render(content(null))
    const trigger = screen.getByRole('button', { name: 'Open evidence' })
    trigger.focus()
    rerender(content('ev_claim'))
    const close = screen.getByRole('button', { name: 'Close evidence panel' })
    const next = screen.getByRole('button', { name: 'Next evidence' })
    expect(close).toHaveFocus()
    await user.tab({ shift: true })
    expect(next).toHaveFocus()
    await user.tab()
    expect(close).toHaveFocus()
    trigger.focus()
    expect(close).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledOnce()
    rerender(content(null))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('preserves distinct external observation and report-read times with cache status', () => {
    render(<CredibilityEvidenceDrawer {...base} selectedId="ev_external" />)
    expect(screen.getByRole('dialog', { name: 'External source' })).toBeInTheDocument()
    expect(screen.getByText('Yes — observation time remains unchanged')).toBeInTheDocument()
    expect(screen.getByText(source.association_reason)).toBeInTheDocument()
    const times = screen.getByRole('dialog').querySelectorAll('time')
    expect(times[0]).toHaveAttribute('dateTime', source.observed_at)
    expect(times[1]).toHaveAttribute('dateTime', source.retrieved_at)
    expect(screen.getByRole('link', { name: 'Open source' })).toHaveAttribute('href', source.url)
    expect(screen.getByRole('button', { name: 'Next evidence' })).toBeDisabled()
  })

  it('labels synthetic example addresses without offering a live link', () => {
    render(<CredibilityEvidenceDrawer {...base} selectedId="ev_external"
      sources={[{ ...source, url: 'https://example.invalid/repository/issue/3' }]} />)
    expect(screen.getByText('Retrieved · Synthetic source')).toBeInTheDocument()
    expect(screen.getByText('This fixture uses an example address. No live page is available.')).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it.each(['javascript:alert(1)', 'data:text/html,test', 'https://user:secret@github.com/team/repo'])
  ('does not turn unsafe source URL %s into a link', (url) => {
    render(<CredibilityEvidenceDrawer {...base} selectedId="ev_external" sources={[{ ...source, url }]} />)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByText('A safe source link is not available.')).toBeInTheDocument()
  })

  it.each([null, 'javascript:alert(1)', 'https://example.invalid/paper.pdf', 'https://example.com/pdf/2310.01798v1'])
  ('does not invent an original-paper link from %s', (paperPdfUrl) => {
    render(<CredibilityEvidenceDrawer {...base} paperPdfUrl={paperPdfUrl} />)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('keeps a failed retrieval visible and marks its excerpt for review', () => {
    render(<CredibilityEvidenceDrawer {...base} selectedId="ev_external"
      sources={[{ ...source, fetch_status: 'timeout', http_status: null }]} />)
    expect(screen.getByText('Request timed out')).toBeInTheDocument()
    expect(screen.getByText('This source was not retrieved successfully. The excerpt needs review.')).toBeInTheDocument()
    expect(screen.queryByText('HTTP status')).not.toBeInTheDocument()
  })

  it('does not open for an unknown evidence id', () => {
    render(<CredibilityEvidenceDrawer {...base} selectedId="missing" />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('closes when the backdrop is clicked', () => {
    const onClose = vi.fn()
    render(<CredibilityEvidenceDrawer {...base} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss evidence panel' }))
    expect(onClose).toHaveBeenCalledOnce()
  })
})
