import type { Assessment } from '../../types/assessment'

export interface DocSection { id: string; label: string; part: 1 | 2 }

/** The document's outline; Part II lists its sections only once the quality report exists. */
export function documentOutline(quality: Assessment | undefined, includeQuality = true): DocSection[] {
  const first: DocSection[] = [
    { id: 'doc-verdict', label: 'Verdict', part: 1 },
    { id: 'doc-claims', label: '1. Claims and evidence', part: 1 },
    { id: 'doc-references', label: '2. References', part: 1 },
    { id: 'doc-code', label: '3. Code and reproduction', part: 1 },
    { id: 'doc-findings', label: '4. Summary of findings', part: 1 },
    { id: 'doc-limits', label: '5. Limitations', part: 1 },
    { id: 'doc-run', label: 'Run details', part: 1 },
  ]
  const second: DocSection[] = quality?.report ? [
    { id: 'doc-quality', label: 'Overview', part: 2 },
    { id: 'doc-dimensions', label: '6. Dimensions', part: 2 },
    { id: 'doc-strengths', label: '7. Strengths and concerns', part: 2 },
    { id: 'doc-claimchecks', label: '8. Claim checks', part: 2 },
    { id: 'doc-contributions', label: '9. Claimed contributions', part: 2 },
    { id: 'doc-repro', label: '10. Reproducibility', part: 2 },
    { id: 'doc-questions', label: '11. Open questions', part: 2 },
    { id: 'doc-qlimits', label: '12. Limitations', part: 2 },
  ] : [{ id: 'doc-quality', label: 'Overview', part: 2 }]
  return includeQuality ? [...first, ...second] : first
}
