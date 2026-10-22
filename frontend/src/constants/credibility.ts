import type { CredibilityDimensionId } from '../types/credibility'

export const dimensionNames: Record<CredibilityDimensionId, string> = {
  claim_evidence: 'Claims & evidence', reference_verification: 'References', code_reproduction: 'Code & reproduction',
}
export const outcomeNames: Record<string, string> = {
  supported: 'Supported', partially_supported: 'Partially supported', not_found: 'No support found in scope', contradicted: 'Contradicted',
  unable_to_determine: 'Unable to determine', not_checked: 'Not checked', matched: 'Matched', metadata_conflict: 'Metadata conflict', unverified: 'Unverified',
  available: 'Code available', not_found_in_scope: 'Not found in checked sources', unable_to_verify: 'Unable to verify', not_applicable: 'Not applicable',
  success_reported: 'Success reported by a third party', failure_reported: 'Failure reported by a third party', discussion_only: 'Discussion only', no_feedback_found: 'No feedback found in scope',
}
export const categoryNames: Record<string, string> = { no_significant_concerns: 'No significant concerns found', some_concerns: 'Some concerns', significant_concerns: 'Significant concerns', critical_concerns: 'Critical concerns' }

export function duration(seconds: number) {
  const total = Math.max(1, Math.round(seconds))
  if (total < 60) return `${total} s`
  return `${Math.floor(total / 60)} min ${String(total % 60).padStart(2, '0')} s`
}
