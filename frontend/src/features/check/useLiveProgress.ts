import { useEffect, useState } from 'react'
import { useScoringFor } from '../../app/jobs'
import { isRunning, useCheckProgress, useScoring } from '../../app/queries'
import type { CredibilityAssessment } from '../../types/credibility'
import { useNow } from '../../ui/motion'
import { describeProgress, type ProgressView } from './progress'

/** Highest percentage shown per check, shared by the waiting page and its tab so both read the same. */
const peaks = new Map<string, number>()

/** Remembers when this browser first saw the checking stage, so estimates survive a refresh. */
function useCheckingSince(assessment: CredibilityAssessment | undefined, now: number) {
  const key = `papertrace-checking-${assessment?.assessment_id ?? ''}`
  const [since, setSince] = useState<number | null>(() => { try { return Number(sessionStorage.getItem(key)) || null } catch { return null } })
  const [seenStage, setSeenStage] = useState(assessment?.stage)
  // Adjusting state during render: the moment the stage turns to checking is the estimate's start.
  if (seenStage !== assessment?.stage) {
    setSeenStage(assessment?.stage)
    if (assessment?.stage === 'checking' && since == null) setSince(now)
  }
  useEffect(() => { if (since != null) try { sessionStorage.setItem(key, String(since)) } catch { /* storage blocked */ } }, [key, since])
  return since
}

/** The quality assessment started beside the check: its own lane, and a small share of the overall percentage. */
export interface QualityLane { id: string; state: 'running' | 'completed' | 'failed'; share: number }
export interface LiveProgress extends ProgressView { elapsed: number; quality: QualityLane | null }

const QUALITY_WEIGHT = 0.1

/** Progress of one check as the waiting page and the title-bar tab show it (spec 4.1). Never moves backwards. */
export function useLiveProgress(assessment: CredibilityAssessment | undefined): LiveProgress | null {
  const running = isRunning(assessment)
  const now = useNow(running)
  const progress = useCheckProgress(assessment?.assessment_id ?? '', running)
  const since = useCheckingSince(assessment, now)
  const qualityId = useScoringFor(assessment?.assessment_id ?? '')
  const scoring = useScoring(running ? qualityId : null)
  if (!assessment) return null
  const view = describeProgress(assessment, progress.data, now, since)
  const value = scoring.data
  const quality: QualityLane | null = !qualityId ? null
    : !value || isRunning(value) ? { id: qualityId, state: 'running', share: Math.max(0.04, (value?.progress_percent ?? 0) / 100) }
    : { id: qualityId, state: value.status === 'completed' && value.report ? 'completed' : 'failed', share: 1 }
  const combined = quality ? view.percent * (1 - QUALITY_WEIGHT) + quality.share * 100 * QUALITY_WEIGHT : view.percent
  const peak = Math.max(peaks.get(assessment.assessment_id) ?? 0, combined)
  peaks.set(assessment.assessment_id, peak)
  return { ...view, quality, percent: assessment.status === 'completed' ? 100 : peak, elapsed: (now - Date.parse(assessment.created_at)) / 1000 }
}
