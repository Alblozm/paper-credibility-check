import type {
  Assessment,
  AssessmentOptions,
  CreateAssessmentRequest,
  ReviewList,
} from '../types/assessment'

export type AssessmentReceipt = Pick<Assessment,
  'assessment_id' | 'status' | 'stage' | 'progress_percent' | 'schema_version' | 'created_at'>

export interface AssessmentApi {
  getOptions(): Promise<AssessmentOptions>
  listAssessments(search?: string, sort?: 'date' | 'title' | 'author', offset?: number): Promise<ReviewList>
  createAssessment(request: CreateAssessmentRequest): Promise<AssessmentReceipt>
  getAssessment(assessmentId: string, signal?: AbortSignal): Promise<Assessment>
  retryAssessment(assessmentId: string): Promise<AssessmentReceipt>
}
