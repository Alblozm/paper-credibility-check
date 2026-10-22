import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import type { Assessment, ModelOption, VenueOption } from '../types/assessment'

const venue: VenueOption = {
  venue_id: 'iclr_2027_main',
  code: 'ICLR',
  name: 'International Conference on Learning Representations',
  year: 2027,
  track: 'main',
  rubric_id: 'iclr_inspired_due_diligence',
  rubric_version: '0.1',
  is_available: true,
}

const model: ModelOption = {
  model_id: 'model_standard_v1',
  display_name: 'Standard analysis',
  provider: 'internal',
  model_version: '2026-08-25',
  description: 'Balances analysis depth against turnaround time.',
  speed_tier: 'standard',
  is_available: true,
  is_default: true,
}

const baseAssessment: Assessment = {
  schema_version: '0.4',
  assessment_id: 'asmt_fixture',
  status: 'completed',
  stage: 'report_ready',
  progress_percent: 100,
  source: {
    type: 'arxiv', arxiv_id: '2501.01234',
    original_url: 'https://arxiv.org/abs/2501.01234',
    upload_id: null, original_filename: null,
  },
  venue,
  model,
  paper: null,
  report: null,
  warnings: [],
  error: null,
  created_at: '2026-08-28T06:27:00Z',
  started_at: '2026-08-28T06:27:03Z',
  completed_at: '2026-08-28T06:30:00Z',
}

window.PCC_MOCK = {
  venues: [venue],
  models: [model],
  stages: [],
  errors: {
    retryable: {
      code: 'MODEL_TIMEOUT', message: 'The analysis model did not respond in time.', stage: 'analyzing',
      retryable: true, debug_id: 'dbg_01',
    },
    nonRetryable: {
      code: 'PDF_PARSE_FAILED', message: 'No readable body text could be extracted from this PDF.', stage: 'parsing_paper',
      retryable: false, debug_id: 'dbg_02',
    },
  },
  completed: structuredClone(baseAssessment),
  provisional: structuredClone(baseAssessment),
}

// Vitest runs without `globals`, so Testing Library's automatic cleanup never
// registers itself. Without this a second render in the same file stacks on top
// of the first and every query finds two of everything.
afterEach(cleanup)
