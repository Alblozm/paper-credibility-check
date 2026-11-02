import type { CredibilityClaimItem, CredibilityReferenceItem } from '../types/credibility'

export const outcomeNames: Record<CredibilityClaimItem['outcome'], string> = {
  supported: 'Supported', partially_supported: 'Partially supported', contradicted: 'Contradicted',
  not_found: 'Not supported in scope', unable_to_determine: 'Undetermined', not_checked: 'Not checked',
}

export const referenceNames: Record<CredibilityReferenceItem['outcome'], string> = {
  matched: 'Matched', metadata_conflict: 'Metadata conflict', unverified: 'Not verified', not_checked: 'Not checked',
}

const reasonLabels: Record<string, string> = {
  source_fetch_failed: 'Source unavailable', target_not_found_in_scope: 'No record found in scope',
  author_mismatch: 'Author mismatch', title_mismatch: 'Title mismatch', year_mismatch: 'Year mismatch',
  identifier_mismatch: 'Identifier mismatch', insufficient_metadata: 'Insufficient metadata', agent_failed: 'Verification incomplete',
}

/** Why a reference stayed unverified; unknown codes are shown as words. */
export const reasonName = (code: string) => reasonLabels[code] ?? code.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase())
