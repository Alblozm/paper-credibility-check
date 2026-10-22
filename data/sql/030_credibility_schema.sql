-- Round-two credibility extension. Run after sql/001_initial_schema.sql. Requires PostgreSQL 14+.
-- It does not alter first-round tables; foreign keys bind every record to the delivered paper version/chunk/element.
BEGIN;
CREATE SCHEMA IF NOT EXISTS credibility_r2;
CREATE TABLE IF NOT EXISTS credibility_r2.paper_version_bindings (
 paper_version_id TEXT PRIMARY KEY REFERENCES public.paper_versions(paper_version_id) ON DELETE CASCADE,
 paper_id TEXT NOT NULL REFERENCES public.papers(paper_id) ON DELETE CASCADE,
 pdf_sha256 TEXT NOT NULL CHECK (pdf_sha256 ~ '^[a-f0-9]{64}$'),
 page_count INTEGER NOT NULL CHECK (page_count > 0), payload JSONB NOT NULL,
 UNIQUE(paper_version_id, pdf_sha256, page_count)
);
CREATE TABLE IF NOT EXISTS credibility_r2.credibility_case_memberships (
 case_id TEXT PRIMARY KEY, paper_version_id TEXT NOT NULL REFERENCES credibility_r2.paper_version_bindings,
 synthetic BOOLEAN NOT NULL, review_status TEXT NOT NULL CHECK(review_status IN ('candidate','reviewed','gold')),
 payload JSONB NOT NULL, CHECK(NOT synthetic OR review_status <> 'gold'),
 UNIQUE(case_id, paper_version_id)
);
CREATE TABLE IF NOT EXISTS credibility_r2.credibility_assessments (
 case_id TEXT NOT NULL, credibility_assessment_id TEXT NOT NULL,
 paper_version_id TEXT NOT NULL, pdf_sha256 TEXT NOT NULL, page_count INTEGER NOT NULL,
 rubric_id TEXT NOT NULL, rubric_version TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status='draft'), scope_complete BOOLEAN NOT NULL,
 overall_score NUMERIC CHECK(overall_score BETWEEN 0 AND 10), overall_category TEXT,
 payload JSONB NOT NULL, PRIMARY KEY(case_id, credibility_assessment_id),
 FOREIGN KEY(case_id, paper_version_id) REFERENCES credibility_r2.credibility_case_memberships(case_id, paper_version_id),
 FOREIGN KEY(paper_version_id,pdf_sha256,page_count) REFERENCES credibility_r2.paper_version_bindings(paper_version_id,pdf_sha256,page_count),
 CHECK((overall_score IS NULL) = (overall_category IS NULL)),
 CHECK(scope_complete OR overall_score IS NULL)
);
CREATE TABLE IF NOT EXISTS credibility_r2.credibility_dimension_scores (
 case_id TEXT NOT NULL, credibility_assessment_id TEXT NOT NULL,
 dimension_id TEXT NOT NULL CHECK(dimension_id IN ('claim_evidence_consistency','reference_verification','code_reproduction_signals')),
 applicability TEXT NOT NULL CHECK(applicability IN ('applicable','not_applicable','unable_to_assess')),
 score NUMERIC CHECK(score BETWEEN 0 AND 10), weight NUMERIC CHECK(weight > 0), payload JSONB NOT NULL,
 PRIMARY KEY(case_id,credibility_assessment_id,dimension_id),
 FOREIGN KEY(case_id,credibility_assessment_id) REFERENCES credibility_r2.credibility_assessments,
 CHECK((applicability='applicable') = (score IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS credibility_r2.credibility_claims (
 case_id TEXT NOT NULL, credibility_claim_id TEXT NOT NULL, paper_version_id TEXT NOT NULL, payload JSONB NOT NULL,
 PRIMARY KEY(case_id,credibility_claim_id),
 FOREIGN KEY(case_id,paper_version_id) REFERENCES credibility_r2.credibility_case_memberships(case_id,paper_version_id)
);
CREATE TABLE IF NOT EXISTS credibility_r2.claim_evidence_checks (
 case_id TEXT NOT NULL, claim_check_id TEXT NOT NULL, credibility_claim_id TEXT NOT NULL,
 verdict TEXT NOT NULL CHECK(verdict IN ('supported','partially_supported','evidence_not_found','contradicted','unable_to_determine')),
 scope_complete BOOLEAN NOT NULL, payload JSONB NOT NULL,
 PRIMARY KEY(case_id,claim_check_id), UNIQUE(case_id,credibility_claim_id),
 FOREIGN KEY(case_id,credibility_claim_id) REFERENCES credibility_r2.credibility_claims
);
CREATE TABLE IF NOT EXISTS credibility_r2.credibility_internal_evidence (
 case_id TEXT NOT NULL, internal_evidence_id TEXT NOT NULL, claim_check_id TEXT NOT NULL,
 chunk_id TEXT REFERENCES public.paper_text_chunks(chunk_id) ON DELETE RESTRICT,
 element_id TEXT REFERENCES public.paper_elements(element_id) ON DELETE RESTRICT, page_number INTEGER NOT NULL CHECK(page_number > 0), payload JSONB NOT NULL,
 PRIMARY KEY(case_id,internal_evidence_id), CHECK(chunk_id IS NOT NULL OR element_id IS NOT NULL),
 FOREIGN KEY(case_id,claim_check_id) REFERENCES credibility_r2.claim_evidence_checks
);
CREATE TABLE IF NOT EXISTS credibility_r2.paper_references (
 case_id TEXT NOT NULL, paper_reference_id TEXT NOT NULL, paper_version_id TEXT NOT NULL,
 sequence_index INTEGER NOT NULL CHECK(sequence_index > 0), parse_status TEXT NOT NULL CHECK(parse_status IN ('success','partial','failed')),
 payload JSONB NOT NULL, PRIMARY KEY(case_id,paper_reference_id), UNIQUE(case_id,sequence_index),
 FOREIGN KEY(case_id,paper_version_id) REFERENCES credibility_r2.credibility_case_memberships(case_id,paper_version_id)
);
CREATE TABLE IF NOT EXISTS credibility_r2.reference_verification_checks (
 case_id TEXT NOT NULL, reference_check_id TEXT NOT NULL, paper_reference_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('matched','metadata_conflict','unverified','parse_failed','not_checked','query_failed')),
 query_attempted BOOLEAN NOT NULL, checked_at TIMESTAMPTZ, payload JSONB NOT NULL,
 PRIMARY KEY(case_id,reference_check_id), UNIQUE(case_id,paper_reference_id),
 FOREIGN KEY(case_id,paper_reference_id) REFERENCES credibility_r2.paper_references,
 CHECK(query_attempted = (status IN ('matched','metadata_conflict','unverified','query_failed'))),
 CHECK(NOT query_attempted OR checked_at IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS credibility_r2.paper_code_repositories (
 case_id TEXT NOT NULL, repository_id TEXT NOT NULL, paper_version_id TEXT NOT NULL,
 association_status TEXT NOT NULL CHECK(association_status IN ('candidate','confirmed','rejected')),
 payload JSONB NOT NULL, PRIMARY KEY(case_id,repository_id),
 FOREIGN KEY(case_id,paper_version_id) REFERENCES credibility_r2.credibility_case_memberships(case_id,paper_version_id)
);
CREATE TABLE IF NOT EXISTS credibility_r2.reproduction_signals (
 case_id TEXT NOT NULL, signal_id TEXT NOT NULL, repository_id TEXT NOT NULL,
 signal_type TEXT NOT NULL CHECK(signal_type IN ('independent_success_report','failure_or_data_problem_report','discussion_inconclusive')),
 payload JSONB NOT NULL, PRIMARY KEY(case_id,signal_id),
 FOREIGN KEY(case_id,repository_id) REFERENCES credibility_r2.paper_code_repositories
);
CREATE TABLE IF NOT EXISTS credibility_r2.credibility_findings (
 case_id TEXT NOT NULL, credibility_assessment_id TEXT NOT NULL, finding_id TEXT NOT NULL,
 dimension_id TEXT NOT NULL CHECK(dimension_id IN ('claim_evidence_consistency','reference_verification','code_reproduction_signals')),
 severity TEXT NOT NULL CHECK(severity IN ('info','low','medium','high','critical')), payload JSONB NOT NULL,
 PRIMARY KEY(case_id,credibility_assessment_id,finding_id),
 FOREIGN KEY(case_id,credibility_assessment_id,dimension_id) REFERENCES credibility_r2.credibility_dimension_scores
);
CREATE INDEX IF NOT EXISTS credibility_reference_status_idx ON credibility_r2.reference_verification_checks(status);
CREATE INDEX IF NOT EXISTS credibility_assessment_version_idx ON credibility_r2.credibility_assessments(paper_version_id);
COMMIT;
-- The redundant hash/page snapshot is intentional: exports remain auditable while the FK guarantees identity with the first-round database.
