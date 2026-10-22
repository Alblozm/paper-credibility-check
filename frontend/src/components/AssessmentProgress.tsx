import { useEffect, useState } from 'react'
import { stageDefinitions } from '../constants/labels'
import type { Assessment } from '../types/assessment'

function elapsedLabel(from: string | null, now: number) {
  if (!from) return null
  const seconds = Math.max(0, Math.round((now - new Date(from).getTime()) / 1000))
  if (seconds < 60) return `${seconds}s elapsed`
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s elapsed`
}

interface Props {
  assessment: Assessment
  onCancel: () => void
}

export function AssessmentProgress({ assessment, onCancel }: Props) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const currentIndex = stageDefinitions.findIndex((item) => item.stage === assessment.stage)
  const current = stageDefinitions[Math.max(0, currentIndex)]
  const queued = assessment.status === 'queued'
  const elapsed = elapsedLabel(assessment.started_at ?? assessment.created_at, now)

  return (
    <section className="view progress-view" aria-labelledby="progress-title">
      <div className="progress-head">
        <div>
          <p className="eyebrow">{queued ? 'Queued' : `Step ${currentIndex + 1} of ${stageDefinitions.length}`}</p>
          <h1 id="progress-title">{queued ? 'Waiting to start' : current.label}</h1>
          <p aria-live="polite">
            {queued ? 'The assessment is in the queue and has not started processing yet.' : current.description}
          </p>
        </div>
        <div className="progress-figure" aria-hidden="true">
          <b>{assessment.progress_percent}</b><span>%</span>
        </div>
      </div>

      <div
        className="meter"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={assessment.progress_percent}
        aria-label="Assessment progress"
      >
        <i style={{ width: `${assessment.progress_percent}%` }} />
      </div>

      <div className="progress-meta">
        <span><code>{assessment.assessment_id}</code></span>
        <span>{elapsed}</span>
      </div>

      <ol className="stepper">
        {stageDefinitions.map((stage, index) => {
          const done = index < currentIndex
          const isCurrent = index === currentIndex && !queued
          return (
            <li className={`stepper-item ${done ? 'is-done' : ''} ${isCurrent ? 'is-current' : ''}`} key={stage.stage}>
              <span className="stepper-dot" aria-hidden="true" />
              <div className="stepper-body">
                <strong>{stage.label}</strong>
                <p>{stage.description}</p>
              </div>
            </li>
          )
        })}
      </ol>

      <div className="progress-meta progress-foot">
        <span>Progress reflects pipeline stages, not report quality.</span>
        <button className="button button-ghost button-sm" type="button" onClick={onCancel}>
          Start a different paper
        </button>
      </div>
    </section>
  )
}
