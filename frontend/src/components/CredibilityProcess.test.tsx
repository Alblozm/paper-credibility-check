import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import paper001 from '../test/fixtures/credibility-paper-001-code.json'
import paper002 from '../test/fixtures/credibility-paper-002.json'
import type { CredibilityAssessment, CredibilityReport } from '../types/credibility'
import { duration } from '../constants/credibility'
import { CredibilityProcess } from './CredibilityProcess'

const report = (value: unknown) => (value as CredibilityAssessment).report as CredibilityReport

describe('CredibilityProcess', () => {
  it('draws the three parallel checks with their real durations and a timed-out reference check', () => {
    render(<CredibilityProcess report={report(paper002)} />)
    const lanes = within(screen.getByRole('list', { name: 'Check timeline' })).getAllByRole('listitem')
    expect(lanes.map(lane => lane.textContent)).toEqual([
      'Claims & evidencePartial · 1 min 40 s',
      'ReferencesFailed · 21 min 58 s · TimeoutError',
      'Code & reproductionCompleted · 2 min 01 s',
    ])
    expect(screen.getByText('Code search agent').nextSibling).toHaveTextContent('3×')
    expect(screen.getByText('"Large Language Models Cannot Self-Correct Reasoning Yet" site:github.com', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('Tool calls').nextSibling).toHaveTextContent('4 / 8')
    expect(screen.getByText(/19 candidate links · 0 verified · 3 rejected/)).toBeInTheDocument()
  })

  it('counts references the agent verified and why the rest stayed unresolved', () => {
    render(<CredibilityProcess report={report(paper001)} />)
    expect(screen.getByText('Of those, found by the reference agent').nextSibling).toHaveTextContent('3')
    expect(screen.getByText('Unresolved · source fetch failed').nextSibling).toHaveTextContent('17')
    expect(screen.getByText('Reference agent: search for sources').nextSibling).toHaveTextContent('3×')
    expect(screen.getByText('Existing source-bound implementation/non-applicability evidence is sufficient.')).toBeInTheDocument()
  })

  it('formats durations for people', () => {
    expect([duration(0.2), duration(65.77), duration(119.6), duration(1317.5)]).toEqual(['1 s', '1 min 06 s', '2 min 00 s', '21 min 58 s'])
  })
})
