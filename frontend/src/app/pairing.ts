import { useMemo } from 'react'
import type { ReviewSummary } from '../types/assessment'
import { checkForScoring, scoringFor, useScoringFor } from './jobs'
import { useCheckHistory, useScoring, useScoringHistory } from './queries'

/* A credibility check and its quality assessment are separate backend records started together. In order of trust:
   1. the server's link: a quality assessment records the check it was started beside (credibility_assessment_id);
   2. the pair the starting browser remembered (jobs.ts), which also covers a quality assessment still running;
   3. for older records with neither, the history: the same paper title, started within ten minutes.
   Matching is one-to-one and nearest-first, so two runs of the same paper never share a quality assessment, and a
   failed quality assessment still belongs to its run. */

const WINDOW_MS = 10 * 60_000
const words = (title: string) => title.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()

/** Titles from two parses of one paper can differ in length ("Instance Normalization:" vs the full title). */
function sameTitle(a: string, b: string) {
  const [x, y] = [words(a), words(b)]
  if (!x || !y) return false
  if (x === y) return true
  return Math.min(x.length, y.length) >= 12 && (x.startsWith(y) || y.startsWith(x))
}

interface CheckLike { assessment_id: string; created_at: string; title?: string | null }

/** Check ID → quality assessment ID for a whole list: server links, then remembered pairs, then nearest matches. */
export function pairAll(checks: CheckLike[], scores: ReviewSummary[], recall: (checkId: string) => string | null = scoringFor) {
  const pairs = new Map<string, string>()
  const claimed = new Set<string>()
  // A linked quality assessment belongs to its run even when that run is not in the list (still running, another page).
  const linked = [...scores].filter(score => score.credibility_assessment_id).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
  for (const score of linked) {
    claimed.add(score.assessment_id)
    // A retried quality assessment is newer than the one it replaces; the newest stands for the run.
    if (!pairs.has(score.credibility_assessment_id!)) pairs.set(score.credibility_assessment_id!, score.assessment_id)
  }
  for (const check of checks) {
    const remembered = recall(check.assessment_id)
    if (remembered && !pairs.has(check.assessment_id) && !claimed.has(remembered)) { pairs.set(check.assessment_id, remembered); claimed.add(remembered) }
  }
  const candidates = checks.filter(check => !pairs.has(check.assessment_id) && check.title).flatMap(check => {
    const at = Date.parse(check.created_at)
    return scores.filter(score => sameTitle(check.title!, score.title) && Math.abs(Date.parse(score.created_at) - at) <= WINDOW_MS)
      .map(score => ({ check: check.assessment_id, score: score.assessment_id, gap: Math.abs(Date.parse(score.created_at) - at) }))
  }).sort((a, b) => a.gap - b.gap)
  for (const { check, score } of candidates) {
    if (pairs.has(check) || claimed.has(score)) continue
    pairs.set(check, score)
    claimed.add(score)
  }
  return pairs
}

/** The run a quality assessment belongs to, if any; `undefined` while the history is still loading. */
export function usePairedCheck(qualityId: string): string | null | undefined {
  const checks = useCheckHistory()
  const scores = useScoringHistory()
  const own = useScoring(qualityId).data
  return useMemo(() => {
    if (own?.credibility_assessment_id) return own.credibility_assessment_id
    const remembered = checkForScoring(qualityId)
    if (remembered) return remembered
    if (checks.isLoading || scores.isLoading) return undefined
    for (const [check, score] of pairAll(checks.data?.items ?? [], scores.data?.items ?? [])) if (score === qualityId) return check
    return null
  }, [qualityId, own, checks.isLoading, scores.isLoading, checks.data, scores.data])
}

/** The quality assessment of a check's run: the server's link, this browser's record, or recognised from the history. */
export function usePairedQuality(check: CheckLike | undefined) {
  // Subscribed so the page updates the moment this browser starts a quality assessment for the check.
  const remembered = useScoringFor(check?.assessment_id ?? '')
  const checks = useCheckHistory()
  const scores = useScoringHistory()
  return useMemo(() => {
    if (!check) return null
    // The open check may be newer than the history page; include it so it can claim its own match.
    const list = [check, ...(checks.data?.items ?? []).filter(item => item.assessment_id !== check.assessment_id)]
    // This browser's record counts unless the server links that assessment to another run.
    const recall = (id: string) => (id === check.assessment_id ? remembered : scoringFor(id))
    return pairAll(list, scores.data?.items ?? [], recall).get(check.assessment_id) ?? null
  }, [remembered, check, checks.data, scores.data])
}
