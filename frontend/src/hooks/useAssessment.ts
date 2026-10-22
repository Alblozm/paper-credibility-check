import { useCallback, useEffect, useRef, useState } from 'react'
import type { AssessmentApi, AssessmentReceipt } from '../api/assessmentApi'
import { ApiError } from '../api/httpAssessmentApi'
import type { Assessment, CreateAssessmentRequest } from '../types/assessment'

export function useAssessment(api: AssessmentApi) {
  const [assessment, setAssessment] = useState<Assessment | null>(null)
  const [receipt, setReceipt] = useState<AssessmentReceipt | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [requestError, setRequestError] = useState<string | null>(null)
  const [queryAttempt, setQueryAttempt] = useState(0)
  const generation = useRef(0)
  const submitting = useRef(false)

  useEffect(() => () => { generation.current += 1 }, [])

  useEffect(() => {
    if (!receipt) return
    let cancelled = false
    let timer: number | undefined
    let failures = 0
    const controller = new AbortController()
    async function poll() {
      try {
        const next = await api.getAssessment(receipt!.assessment_id, controller.signal)
        if (cancelled) return
        failures = 0
        setAssessment(next)
        setRequestError(null)
        if (['queued', 'processing'].includes(next.status)) {
          timer = window.setTimeout(poll, 2000)
        }
      } catch (error) {
        if (cancelled) return
        failures += 1
        const message = error instanceof Error ? error.message : 'Could not refresh the assessment status.'
        const permanent = error instanceof ApiError
          && (error.code === 'INVALID_RESPONSE' || (error.status >= 400 && error.status < 500 && error.status !== 429))
        const stopped = permanent || failures >= 3
        setRequestError(`${message}${stopped ? ' Polling is paused — resume it, or go back and submit again.' : ' Reconnecting…'}`)
        if (!stopped) timer = window.setTimeout(poll, 2000 * 2 ** failures)
      }
    }
    void poll()
    return () => {
      cancelled = true
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [api, receipt, queryAttempt])

  const start = useCallback(async (action: () => Promise<AssessmentReceipt>) => {
    if (submitting.current) return
    submitting.current = true
    const current = ++generation.current
    setIsSubmitting(true)
    setRequestError(null)
    try {
      const created = await action()
      if (current !== generation.current) return
      setAssessment(null)
      setReceipt(created)
    } catch (error) {
      if (current === generation.current) {
        setRequestError(error instanceof Error ? error.message : 'Could not create the assessment.')
      }
    } finally {
      submitting.current = false
      if (current === generation.current) setIsSubmitting(false)
    }
  }, [])

  const submit = useCallback((request: CreateAssessmentRequest) =>
    start(() => api.createAssessment(request)), [api, start])

  const retry = useCallback(async () => {
    if (assessment) await start(() => api.retryAssessment(assessment.assessment_id))
  }, [api, assessment, start])

  const resume = useCallback(() => {
    setRequestError(null)
    setQueryAttempt((value) => value + 1)
  }, [])

  const reset = useCallback(() => {
    generation.current += 1
    setAssessment(null)
    setReceipt(null)
    setIsSubmitting(false)
    setRequestError(null)
  }, [])

  const restore = useCallback((saved: Assessment) => {
    generation.current += 1
    setRequestError(null)
    setIsSubmitting(false)
    setAssessment(saved)
    setReceipt(saved.status === 'queued' || saved.status === 'processing' ? saved : null)
  }, [])

  return { assessment, receipt, isSubmitting, requestError, submit, retry, reset, resume, restore }
}
