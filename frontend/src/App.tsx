import { useCallback, useEffect, useRef, useState } from 'react'
import { httpAssessmentApi } from './api/httpAssessmentApi'
import { httpCredibilityApi } from './api/httpCredibilityApi'
import { mockAssessmentApi } from './api/mockAssessmentApi'
import { mockCredibilityApi } from './api/mockCredibilityApi'
import { AssessmentErrorView } from './components/AssessmentErrorView'
import { AssessmentProgress } from './components/AssessmentProgress'
import { AssessmentReport } from './components/AssessmentReport'
import { CredibilityWorkspace, type RunningCheck } from './components/CredibilityWorkspace'
import { InputWorkspace } from './components/InputWorkspace'
import { LandingPage } from './components/LandingPage'
import { ReportsPage } from './components/ReportsPage'
import { useAssessment } from './hooks/useAssessment'
import type { Assessment, AssessmentOptions, MockScenario } from './types/assessment'
import type { CredibilityAssessment } from './types/credibility'

const isMock = import.meta.env.VITE_API_MODE === 'mock'
const api = isMock ? mockAssessmentApi : httpAssessmentApi
const credibilityApi = isMock ? mockCredibilityApi : httpCredibilityApi

type Section = 'check' | 'scoring' | 'reports'
const sectionHash: Record<Section, string> = { check: '#check', scoring: '#scoring', reports: '#reports' }
// '#credibility' is the old check address; keep it working for saved links.
const sectionFromHash = (hash: string): Section => hash === '#scoring' ? 'scoring' : hash === '#reports' ? 'reports' : 'check'

interface ScoringProps {
  openRequest: { assessment: Assessment; nonce: number } | null
  homeRequest: number
}

/** Five-dimension paper scoring, now one section of the shared shell. */
function ScoringSection({ openRequest, homeRequest }: ScoringProps) {
  const [options, setOptions] = useState<AssessmentOptions | null>(null)
  const [optionsError, setOptionsError] = useState<string | null>(null)
  const [scenario, setScenario] = useState<MockScenario>('completed')
  const [hasEntered, setHasEntered] = useState(false)
  const { assessment, receipt, isSubmitting, requestError, submit, retry, reset, resume, restore } = useAssessment(api)

  const [seenOpen, setSeenOpen] = useState(openRequest)
  if (openRequest !== seenOpen) {
    setSeenOpen(openRequest)
    if (openRequest) { restore(openRequest.assessment); setHasEntered(true) }
  }

  const homeSeen = useRef(homeRequest)
  useEffect(() => {
    if (homeRequest === homeSeen.current) return
    homeSeen.current = homeRequest
    reset()
    setHasEntered(false)
  }, [homeRequest, reset])

  const loadOptions = useCallback(async () => {
    setOptionsError(null)
    try {
      setOptions(await api.getOptions())
    } catch (error) {
      setOptionsError(error instanceof Error ? error.message : 'Assessment options are unavailable.')
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    void api.getOptions().then((nextOptions) => {
      if (!cancelled) setOptions(nextOptions)
    }).catch((error: unknown) => {
      if (!cancelled) {
        setOptionsError(error instanceof Error ? error.message : 'Assessment options are unavailable.')
      }
    })
    return () => { cancelled = true }
  }, [])

  function updateScenario(next: MockScenario) {
    setScenario(next)
    mockAssessmentApi.setScenario(next)
  }

  // Fixture mode is a backend stub: the report is canned, so say so before
  // anyone reads a score off it in a meeting.
  const isFixtureBackend = !isMock && Boolean(options?.models.some((item) => item.model_id === 'model_fixture_v1'))

  function startNew() {
    reset()
    setHasEntered(true)
  }

  // The landing page is only ever the entry point; an in-flight or finished
  // assessment always wins so a poll result can never be hidden behind it.
  const onLanding = !hasEntered && !assessment && !receipt

  // Each view is a separate screen, not a section of one page, so a change of
  // view starts at the top rather than inheriting the previous scroll offset.
  const viewKey = !options ? 'boot'
    : onLanding ? 'landing'
    : assessment ? assessment.status
    : receipt ? 'queued'
    : 'compose'

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' as ScrollBehavior })
  }, [viewKey])

  let content: React.ReactNode
  if (!options) {
    content = (
      <section className="view slate" aria-live="polite">
        {optionsError ? (
          <>
            <h1>Workspace unavailable</h1>
            <p>{optionsError}</p>
            <button className="button button-primary" type="button" onClick={loadOptions}>Try again</button>
          </>
        ) : (
          <>
            <div className="spinner" aria-hidden="true" />
            <h1>Loading workspace</h1>
            <p>Reading the available rubrics and analysis models.</p>
          </>
        )}
      </section>
    )
  } else if (receipt && !assessment) {
    content = (
      <section className="view slate" aria-live="polite">
        <div className="spinner" aria-hidden="true" />
        <h1>Assessment queued</h1>
        <p>Waiting for the first status update from the backend.</p>
        <span className="slate-id">{receipt.assessment_id}</span>
        {requestError && <p className="field-error" role="alert">{requestError}</p>}
        <div className="slate-actions">
          {requestError && <button className="button button-primary" type="button" onClick={resume}>Resume polling</button>}
          <button className="button button-secondary" type="button" onClick={reset}>Back to input</button>
        </div>
      </section>
    )
  } else if (onLanding) {
    content = <LandingPage onStart={startNew} />
  } else if (!assessment) {
    content = (
      <InputWorkspace
        options={options}
        isSubmitting={isSubmitting}
        requestError={requestError}
        showMockControls={isMock}
        scenario={scenario}
        onScenarioChange={updateScenario}
        onSubmit={submit}
      />
    )
  } else if (assessment.status === 'queued' || assessment.status === 'processing') {
    content = (
      <>
        {requestError && (
          <div className="notice notice-amber notice-block" role="alert">
            <span>{requestError}</span>
            <span className="notice-actions">
              <button className="button button-secondary button-sm" type="button" onClick={resume}>Resume</button>
              <button className="button button-ghost button-sm" type="button" onClick={reset}>Cancel</button>
            </span>
          </div>
        )}
        <AssessmentProgress assessment={assessment} onCancel={reset} />
      </>
    )
  } else if (assessment.status === 'failed') {
    content = (
      <AssessmentErrorView
        assessment={assessment}
        isRetrying={isSubmitting}
        requestError={requestError}
        onRetry={retry}
        onBack={reset}
      />
    )
  } else {
    content = <AssessmentReport assessment={assessment} onNewAssessment={startNew} />
  }

  return (
    <>
      {!onLanding && (isMock || isFixtureBackend) && (
        <div className="notice notice-amber notice-block" role="status">
          <span>
            <strong>Sample output.</strong>{' '}
            {isMock
              ? 'The frontend is running against local demo data — nothing is sent to the backend.'
              : 'The backend is serving a fixed fixture report. Scores below do not describe the submitted paper.'}
          </span>
        </div>
      )}
      {content}
    </>
  )
}

function App() {
  const [section, setSection] = useState<Section>(() => sectionFromHash(window.location.hash))
  const [running, setRunning] = useState<RunningCheck | null>(null)
  const [scoringOpen, setScoringOpen] = useState<ScoringProps['openRequest']>(null)
  const [credibilityOpen, setCredibilityOpen] = useState<{ assessment: CredibilityAssessment; nonce: number } | null>(null)
  const [home, setHome] = useState({ check: 0, scoring: 0 })

  useEffect(() => {
    const update = () => setSection(sectionFromHash(window.location.hash))
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])

  const navigate = useCallback((next: Section, { home: toHome = false } = {}) => {
    setSection(next)
    if (window.location.hash !== sectionHash[next]) window.history.pushState(null, '', sectionHash[next])
    if (toHome && next !== 'reports') setHome(value => ({ ...value, [next]: value[next] + 1 }))
    window.scrollTo({ top: 0, behavior: 'instant' as ScrollBehavior })
  }, [])

  const openScoring = useCallback((assessment: Assessment) => {
    setScoringOpen({ assessment, nonce: Date.now() })
    navigate('scoring')
  }, [navigate])

  const openCredibility = useCallback((assessment: CredibilityAssessment) => {
    setCredibilityOpen({ assessment, nonce: Date.now() })
    navigate('check')
  }, [navigate])

  const nav: { id: Section; long: string; short: string }[] = [
    { id: 'check', long: 'Credibility check', short: 'Check' },
    { id: 'scoring', long: 'Paper scoring', short: 'Scoring' },
    { id: 'reports', long: 'Reports', short: 'Reports' },
  ]

  // Every section stays mounted so a running check or score keeps polling while another is on screen.
  return (
    <div className="app-shell">
      <header className="app-bar">
        <div className="app-bar-inner">
          <button className="brand" type="button" onClick={() => navigate('check', { home: true })} aria-label="Paper Credibility Check home">
            <span className="brand-mark" aria-hidden="true">PC</span>
            <span className="brand-name">Paper Credibility Check</span>
          </button>
          <nav className="app-nav" aria-label="Sections">
            {nav.map(item => (
              <button key={item.id} type="button" className="app-nav-item" aria-current={section === item.id ? 'page' : undefined}
                onClick={() => navigate(item.id, { home: section === item.id })}>
                <span className="app-nav-long">{item.long}</span><span className="app-nav-short">{item.short}</span>
              </button>
            ))}
          </nav>
          <span className="bar-spacer" />
          {running && (
            <button className="bar-running" type="button" onClick={() => navigate('check')} title={running.label}
              aria-label={`Check running: ${running.stage}, ${running.progress}%. Show progress`}>
              <i aria-hidden="true" /><span className="bar-running-label">{running.stage}</span> {running.progress}%
            </button>
          )}
          <span className={`env-badge ${isMock ? 'is-demo' : ''}`}>
            <i aria-hidden="true" />
            <span className="env-badge-label">{isMock ? 'Demo data' : 'Live backend'}</span>
          </span>
        </div>
      </header>
      <main className="app-main">
        <div hidden={section !== 'check'}>
          <CredibilityWorkspace api={credibilityApi} scoringApi={api} live={!isMock} openRequest={credibilityOpen}
            homeRequest={home.check} onOpenScoring={openScoring} onRunningChange={setRunning} />
        </div>
        <div hidden={section !== 'scoring'}>
          <ScoringSection openRequest={scoringOpen} homeRequest={home.scoring} />
        </div>
        {section === 'reports' && (
          <ReportsPage scoringApi={api} credibilityApi={credibilityApi} onOpenScoring={openScoring}
            onOpenCredibility={openCredibility} onNew={() => navigate('check', { home: true })} />
        )}
      </main>
    </div>
  )
}

export default App
