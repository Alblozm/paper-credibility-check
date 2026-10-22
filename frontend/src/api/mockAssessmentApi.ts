import type { AssessmentApi } from './assessmentApi'
import type {
  Assessment,
  AssessmentError,
  CreateAssessmentRequest,
  MockScenario,
} from '../types/assessment'

type MockRecord = {
  assessment: Assessment
  tick: number
  scenario: MockScenario
}

const progress = [
  { stage: 'validating_input', percent: 8 },
  { stage: 'retrieving_paper', percent: 22 },
  { stage: 'parsing_paper', percent: 38 },
  { stage: 'analyzing', percent: 58 },
  { stage: 'linking_evidence', percent: 76 },
  { stage: 'finalizing_report', percent: 92 },
  { stage: 'report_ready', percent: 100 },
] as const

const records = new Map<string, MockRecord>()
let selectedScenario: MockScenario = 'completed'

function wait(ms = 180) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function clone<T>(value: T): T {
  return structuredClone(value)
}

function createId() {
  return `asmt_mock_${crypto.randomUUID().slice(0, 8)}`
}

function resolveFixture(scenario: MockScenario) {
  return scenario === 'provisional'
    ? clone(window.PCC_MOCK.provisional)
    : clone(window.PCC_MOCK.completed)
}

function applyRequest(fixture: Assessment, request: CreateAssessmentRequest) {
  const venue = window.PCC_MOCK.venues.find((item) => item.venue_id === request.venue_id)
  const model = window.PCC_MOCK.models.find((item) => item.model_id === request.model_id)
  if (!venue || !model) throw new Error('The selected rubric or model is not available.')

  fixture.venue = clone(venue)
  fixture.model = clone(model)
  if (request.source_type === 'arxiv') {
    fixture.source = {
      type: 'arxiv',
      arxiv_id: request.arxiv_id,
      original_url: `https://arxiv.org/abs/${request.arxiv_id}`,
      upload_id: null,
      original_filename: null,
    }
    if (fixture.paper) fixture.paper.arxiv_id = request.arxiv_id
  } else {
    fixture.source = {
      type: 'pdf',
      arxiv_id: null,
      original_url: null,
      upload_id: `upl_mock_${crypto.randomUUID().slice(0, 8)}`,
      original_filename: request.file.name,
    }
    if (fixture.paper) fixture.paper.arxiv_id = null
  }
}

function queuedAssessment(request: CreateAssessmentRequest, scenario: MockScenario) {
  const fixture = resolveFixture(scenario)
  applyRequest(fixture, request)
  fixture.assessment_id = createId()
  fixture.status = 'queued'
  fixture.stage = 'validating_input'
  fixture.progress_percent = 0
  fixture.paper = null
  fixture.report = null
  fixture.warnings = []
  fixture.error = null
  fixture.created_at = new Date().toISOString()
  fixture.started_at = null
  fixture.completed_at = null
  return fixture
}

function failedAssessment(record: MockRecord, error: AssessmentError) {
  record.assessment.status = 'failed'
  record.assessment.stage = error.stage ?? 'analyzing'
  record.assessment.error = clone(error)
  record.assessment.report = null
  record.assessment.completed_at = new Date().toISOString()
}

export const mockAssessmentApi: AssessmentApi & {
  setScenario(scenario: MockScenario): void
} = {
  async listAssessments(search = '', sort = 'date', offset = 0) {
    await wait()
    const term = search.toLowerCase().trim()
    const items = Array.from(records.values())
      .map((record) => record.assessment)
      .filter((item) => ['completed', 'failed'].includes(item.status))
      .map((item) => ({
        assessment_id: item.assessment_id,
        status: item.status as 'completed' | 'failed',
        title: item.paper?.title ?? item.source.original_filename ?? item.source.arxiv_id ?? 'Untitled paper',
        authors: item.paper?.authors ?? [],
        source_type: item.source.type,
        venue: item.venue,
        model: item.model,
        overall_score: item.report?.overall_assessment.overall_score ?? null,
        rating: item.report?.overall_assessment.rating ?? null,
        is_demo: true,
        created_at: item.created_at,
        completed_at: item.completed_at,
      }))
      .filter((item) => `${item.title} ${item.authors.join(' ')} ${item.venue.code}`.toLowerCase().includes(term))
      .sort((a, b) => sort === 'title' ? a.title.localeCompare(b.title)
        : sort === 'author' ? a.authors.join(' ').localeCompare(b.authors.join(' '))
        : b.created_at.localeCompare(a.created_at))
    return { items: clone(items.slice(offset, offset + 20)), total: items.length, offset, limit: 20 }
  },

  setScenario(scenario) {
    selectedScenario = scenario
  },

  async getOptions() {
    await wait()
    return {
      venues: clone(window.PCC_MOCK.venues.filter((item) => item.is_available)),
      models: clone(window.PCC_MOCK.models),
    }
  },

  async createAssessment(request) {
    await wait(320)
    const assessment = queuedAssessment(request, selectedScenario)
    records.set(assessment.assessment_id, {
      assessment,
      tick: -1,
      scenario: selectedScenario,
    })
    return clone(assessment)
  },

  async getAssessment(assessmentId) {
    await wait()
    const record = records.get(assessmentId)
    if (!record) throw new Error('No assessment with that ID.')
    if (['completed', 'failed'].includes(record.assessment.status)) return clone(record.assessment)
    record.tick += 1

    if (record.scenario === 'retryable_error' && record.tick >= 3) {
      failedAssessment(record, window.PCC_MOCK.errors.retryable)
      return clone(record.assessment)
    }
    if (record.scenario === 'non_retryable_error' && record.tick >= 2) {
      failedAssessment(record, window.PCC_MOCK.errors.nonRetryable)
      return clone(record.assessment)
    }

    const step = progress[Math.min(record.tick, progress.length - 1)]
    record.assessment.status = step.percent === 100 ? 'completed' : 'processing'
    record.assessment.stage = step.stage
    record.assessment.progress_percent = step.percent
    record.assessment.started_at ??= new Date().toISOString()

    if (step.percent === 100) {
      const completed = resolveFixture(record.scenario)
      completed.assessment_id = record.assessment.assessment_id
      completed.source = clone(record.assessment.source)
      completed.venue = clone(record.assessment.venue)
      completed.model = clone(record.assessment.model)
      completed.created_at = record.assessment.created_at
      completed.started_at = record.assessment.started_at
      completed.completed_at = new Date().toISOString()
      record.assessment = completed
    }
    return clone(record.assessment)
  },

  async retryAssessment(assessmentId) {
    await wait(260)
    const record = records.get(assessmentId)
    if (!record) throw new Error('No assessment with that ID.')
    if (!record.assessment.error?.retryable) throw new Error('This failure cannot be retried directly.')
    record.tick = -1
    record.scenario = 'completed'
    record.assessment.status = 'queued'
    record.assessment.stage = 'validating_input'
    record.assessment.progress_percent = 0
    record.assessment.error = null
    record.assessment.completed_at = null
    return clone(record.assessment)
  },
}

export function resetMockApiForTests() {
  records.clear()
  selectedScenario = 'completed'
}
