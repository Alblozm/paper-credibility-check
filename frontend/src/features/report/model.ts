import type {
  CredibilityClaimItem, CredibilityDimension, CredibilityDimensionId, CredibilityEvidence, CredibilityReferenceItem, CredibilityReport,
} from '../../types/credibility'
import type { IconName } from '../../ui/Icon'

export type SkillId = 'claims' | 'refs' | 'code'
export type MarkKind = 'support' | 'claim' | 'ref-ok' | 'ref-gap'
export interface Claim extends CredibilityClaimItem { label: string }

/** One highlight per evidence passage; a passage can serve several claims or references. */
export interface Mark {
  id: string
  evidence: CredibilityEvidence
  page: number
  kind: MarkKind
  skill: SkillId
  claims: Claim[]
  refs: CredibilityReferenceItem[]
}

export interface Skill {
  id: SkillId
  name: string
  short: string
  dimension: CredibilityDimensionId
  icon: IconName
  legend: [MarkKind, string][]
}

/** Each check is a skill: a score, a widget and a highlight layer. New checks register here. */
export const skills: Skill[] = [
  { id: 'claims', name: 'Claims & evidence', short: 'Claims', dimension: 'claim_evidence', icon: 'text_quote',
    legend: [['support', 'Passage that supports the judgment'], ['claim', 'Where the paper states the claim']] },
  { id: 'refs', name: 'References', short: 'References', dimension: 'reference_verification', icon: 'book_open',
    legend: [['ref-ok', 'Matched to a bibliographic source'], ['ref-gap', 'Could not verify']] },
  { id: 'code', name: 'Code & reproduction', short: 'Code', dimension: 'code_reproduction', icon: 'code', legend: [] },
]

/** Rubric 0.2 bands, used for the gaps on the score arc. */
export const bandEdges = [3, 6, 8]

export interface Section { title: string; page: number; claims: number; refs: number }

export interface ReportModel {
  claims: Claim[]
  marks: Map<string, Mark>
  evidence: Map<string, CredibilityEvidence>
  references: Map<string, CredibilityReferenceItem>
  dimensions: Record<CredibilityDimensionId, CredibilityDimension>
  fallback: Set<CredibilityDimensionId>
  sections: Section[]
  concerns: number
  gaps: number
}

export function buildModel(report: CredibilityReport): ReportModel {
  const evidence = new Map(report.evidence.map(item => [item.evidence_id, item]))
  const claims = report.checks.claims.items.map((item, index) => ({ ...item, label: `C${index + 1}` }))
  const references = new Map(report.checks.references.items.map(item => [item.reference_id, item]))
  const marks = new Map<string, Mark>()
  const add = (id: string, kind: MarkKind, skill: SkillId, extra: { claim?: Claim; ref?: CredibilityReferenceItem }) => {
    const item = evidence.get(id)
    if (!item?.paper_locator) return
    const mark = marks.get(id) ?? { id, evidence: item, page: item.paper_locator.page_start, kind, skill, claims: [], refs: [] }
    if (extra.claim && !mark.claims.includes(extra.claim)) mark.claims.push(extra.claim)
    if (extra.ref) mark.refs.push(extra.ref)
    if (kind === 'support') mark.kind = 'support'
    marks.set(id, mark)
  }
  claims.forEach(claim => {
    claim.claim_evidence_ids.forEach(id => add(id, 'claim', 'claims', { claim }))
    claim.comparison_evidence_ids.forEach(id => add(id, 'support', 'claims', { claim }))
  })
  report.checks.references.items.forEach(ref => ref.paper_evidence_ids.forEach(id =>
    add(id, ref.outcome === 'matched' ? 'ref-ok' : 'ref-gap', 'refs', { ref })))

  const sections: Section[] = []
  ;[...marks.values()].filter(mark => mark.skill === 'claims' && mark.evidence.paper_locator?.section_title)
    .sort((a, b) => a.page - b.page)
    .forEach(mark => {
      const title = mark.evidence.paper_locator!.section_title!
      const section = sections.find(item => item.title === title)
      if (section) section.claims++
      else sections.push({ title, page: mark.page, claims: 1, refs: 0 })
    })
  const refMarks = [...marks.values()].filter(mark => mark.skill === 'refs')
  if (refMarks.length) sections.push({ title: 'References', page: Math.min(...refMarks.map(mark => mark.page)), claims: 0, refs: refMarks.length })

  return {
    claims, marks, evidence, references, sections,
    dimensions: Object.fromEntries(report.dimensions.map(item => [item.dimension_id, item])) as ReportModel['dimensions'],
    fallback: new Set(report.overall.calculation.fallback_dimension_ids ?? []),
    concerns: report.findings.filter(item => item.kind === 'concern').length,
    gaps: report.findings.filter(item => item.kind === 'verification_gap').length,
  }
}

export const markCount = (model: ReportModel, skill: SkillId) => [...model.marks.values()].filter(mark => mark.skill === skill).length

export const pagesOf = (model: ReportModel, ids: string[]) =>
  [...new Set(ids.map(id => model.evidence.get(id)?.paper_locator?.page_start).filter((page): page is number => page != null))].sort((a, b) => a - b)
