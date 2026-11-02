import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { ApiError } from '../api/httpAssessmentApi'
import { credibilityModelChoice } from '../api/httpCredibilityApi'
import type { CredibilityAssessment } from '../types/credibility'
import { credibilityApi, getCheckProgress, isMock, scoringApi } from './api'
import { forgetQuality } from './jobs'

export const isRunning = (value?: { status: string } | null) => value?.status === 'queued' || value?.status === 'processing'

/** A long check outlives brief network drops; only contract and client errors stop polling. */
const transient = (error: unknown) => !(error instanceof ApiError
  && (error.code === 'INVALID_RESPONSE' || (error.status >= 400 && error.status < 500 && error.status !== 429)))
const retry = (failures: number, error: unknown) => transient(error) && failures < 5
const retryDelay = (attempt: number) => Math.min(2000 * 2 ** attempt, 30_000)
const pollMs = isMock ? 450 : 2000

/** One credibility check, polled while it runs; shared by its page and its title-bar tab. */
export const checkQuery = (id: string) => ({
  queryKey: ['check', id],
  queryFn: ({ signal }: { signal: AbortSignal }) => credibilityApi.getAssessment(id, signal),
  refetchInterval: (query: { state: { data?: CredibilityAssessment } }) => (isRunning(query.state.data) ? pollMs : false),
  refetchIntervalInBackground: true,
  retry, retryDelay,
})

export function useCheck(id: string) {
  return useQuery(checkQuery(id))
}

export function useCheckProgress(id: string, running: boolean) {
  return useQuery({
    queryKey: ['check-progress', id],
    queryFn: ({ signal }) => getCheckProgress(id, signal),
    enabled: running && !isMock,
    refetchInterval: running ? 3000 : false,
    refetchIntervalInBackground: true,
    retry: 1,
  })
}

export function useScoring(id: string | null) {
  const query = useQuery({
    queryKey: ['score', id],
    queryFn: ({ signal }) => scoringApi.getAssessment(id!, signal),
    enabled: Boolean(id),
    refetchInterval: query => (isRunning(query.state.data) ? pollMs : false),
    refetchIntervalInBackground: true,
    retry, retryDelay,
  })
  // A deleted quality assessment is gone for good: forget the pair rather than show it as running forever.
  const gone = query.error instanceof ApiError && query.error.status === 404
  useEffect(() => { if (gone && id) forgetQuality(id) }, [gone, id])
  return query
}

/** Live models for checks; the fixture backend offers only an offline sample. */
export function useModels() {
  return useQuery({
    queryKey: ['models'],
    queryFn: async () => credibilityModelChoice(await credibilityApi.getModels!()),
    enabled: !isMock && Boolean(credibilityApi.getModels),
    staleTime: Infinity,
  })
}

export function useCheckHistory(search = '') {
  return useQuery({
    queryKey: ['check-history', search],
    queryFn: () => credibilityApi.listAssessments!(search, 0),
    enabled: Boolean(credibilityApi.listAssessments),
  })
}

export function useScoringHistory(search = '') {
  return useQuery({
    queryKey: ['score-history', search],
    queryFn: () => scoringApi.listAssessments(search, 'date', 0),
  })
}
