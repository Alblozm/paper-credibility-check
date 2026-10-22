BEGIN;

CREATE TABLE papers (
    paper_id TEXT PRIMARY KEY,
    title TEXT,
    authors JSONB NOT NULL DEFAULT '[]'::jsonb,
    authors_status TEXT NOT NULL DEFAULT 'missing_requires_review'
        CHECK (authors_status IN ('verified', 'anonymous_at_assessed_version', 'missing_requires_review')),
    abstract TEXT,
    language TEXT,
    arxiv_id TEXT,
    doi TEXT,
    openreview_forum_id TEXT,
    venue TEXT,
    publication_year INTEGER,
    track TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT papers_authors_array CHECK (jsonb_typeof(authors) = 'array')
);

CREATE UNIQUE INDEX papers_arxiv_id_unique
    ON papers (arxiv_id) WHERE arxiv_id IS NOT NULL;
CREATE UNIQUE INDEX papers_openreview_forum_unique
    ON papers (openreview_forum_id) WHERE openreview_forum_id IS NOT NULL;

CREATE TABLE paper_versions (
    paper_version_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    version_label TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('arxiv', 'openreview', 'pdf')),
    source_url TEXT,
    pdf_object_key TEXT NOT NULL,
    pdf_sha256 CHAR(64) NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    page_count INTEGER CHECK (page_count IS NULL OR page_count > 0),
    parser_name TEXT,
    parser_version TEXT,
    parse_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (parse_status IN ('pending', 'processing', 'completed', 'failed')),
    parse_quality TEXT CHECK (parse_quality IN ('good', 'partial', 'poor')),
    parse_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    has_ocr BOOLEAN NOT NULL DEFAULT false,
    reading_order_quality TEXT CHECK (reading_order_quality IN ('high', 'medium', 'low')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (paper_id, version_label, source_type),
    UNIQUE (pdf_sha256),
    CONSTRAINT paper_versions_warnings_array CHECK (jsonb_typeof(parse_warnings) = 'array')
);

CREATE TABLE paper_sections (
    section_id TEXT PRIMARY KEY,
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(paper_version_id) ON DELETE CASCADE,
    parent_section_id TEXT REFERENCES paper_sections(section_id) ON DELETE SET NULL,
    section_number TEXT,
    title TEXT NOT NULL,
    section_role TEXT,
    level INTEGER NOT NULL CHECK (level >= 0),
    sequence_index INTEGER NOT NULL CHECK (sequence_index >= 0),
    page_start INTEGER,
    page_end INTEGER,
    UNIQUE (paper_version_id, sequence_index),
    CHECK (page_start IS NULL OR page_start > 0),
    CHECK (page_end IS NULL OR page_end >= page_start)
);

CREATE TABLE paper_text_chunks (
    chunk_id TEXT PRIMARY KEY,
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(paper_version_id) ON DELETE CASCADE,
    section_id TEXT REFERENCES paper_sections(section_id) ON DELETE SET NULL,
    sequence_index INTEGER NOT NULL CHECK (sequence_index >= 0),
    chunk_index_in_section INTEGER CHECK (chunk_index_in_section IS NULL OR chunk_index_in_section >= 0),
    page_start INTEGER,
    page_end INTEGER,
    content_type TEXT NOT NULL DEFAULT 'body_text',
    text_content TEXT NOT NULL CHECK (length(btrim(text_content)) > 0),
    token_count INTEGER CHECK (token_count IS NULL OR token_count > 0),
    char_count INTEGER NOT NULL CHECK (char_count > 0),
    location_confidence TEXT NOT NULL CHECK (location_confidence IN ('high', 'medium', 'low')),
    UNIQUE (paper_version_id, sequence_index),
    CHECK (page_start IS NULL OR page_start > 0),
    CHECK (page_end IS NULL OR page_end >= page_start)
);

CREATE INDEX paper_text_chunks_section_idx ON paper_text_chunks(section_id);
CREATE INDEX paper_text_chunks_version_idx ON paper_text_chunks(paper_version_id);

CREATE TABLE paper_elements (
    element_id TEXT PRIMARY KEY,
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(paper_version_id) ON DELETE CASCADE,
    section_id TEXT REFERENCES paper_sections(section_id) ON DELETE SET NULL,
    element_type TEXT NOT NULL CHECK (element_type IN ('figure', 'table', 'equation', 'theorem')),
    label TEXT,
    caption TEXT,
    page_number INTEGER CHECK (page_number IS NULL OR page_number > 0),
    extracted_text TEXT,
    asset_object_key TEXT,
    parse_quality TEXT NOT NULL CHECK (parse_quality IN ('good', 'partial', 'poor'))
);

CREATE TABLE paper_links (
    paper_link_id TEXT PRIMARY KEY,
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(paper_version_id) ON DELETE CASCADE,
    source_chunk_id TEXT REFERENCES paper_text_chunks(chunk_id) ON DELETE SET NULL,
    link_type TEXT NOT NULL CHECK (link_type IN ('code', 'data', 'project', 'appendix', 'other')),
    url TEXT NOT NULL,
    access_status TEXT CHECK (access_status IN ('accessible', 'unavailable', 'unchecked')),
    checked_at TIMESTAMPTZ
);

CREATE TABLE paper_semantic_descriptors (
    descriptor_id TEXT PRIMARY KEY,
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(paper_version_id) ON DELETE CASCADE,
    descriptor_group TEXT NOT NULL CHECK (descriptor_group IN (
        'research_problem', 'contributions', 'claims', 'method', 'evaluation',
        'reliability_checks', 'limitations'
    )),
    descriptor_type TEXT NOT NULL,
    descriptor_text TEXT NOT NULL CHECK (length(btrim(descriptor_text)) > 0),
    evidence_chunk_id TEXT REFERENCES paper_text_chunks(chunk_id) ON DELETE SET NULL,
    evidence_element_id TEXT REFERENCES paper_elements(element_id) ON DELETE SET NULL,
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    CHECK (evidence_chunk_id IS NOT NULL OR evidence_element_id IS NOT NULL)
);

CREATE INDEX paper_semantic_descriptors_version_idx
    ON paper_semantic_descriptors(paper_version_id);

CREATE TABLE rubric_versions (
    rubric_id TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    name TEXT NOT NULL,
    definition JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'retired')),
    effective_at TIMESTAMPTZ,
    PRIMARY KEY (rubric_id, rubric_version)
);

CREATE TABLE model_runs (
    model_run_id TEXT PRIMARY KEY,
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(paper_version_id),
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    rubric_id TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
    raw_output_object_key TEXT,
    raw_output_sha256 TEXT CHECK (raw_output_sha256 IS NULL OR raw_output_sha256 ~ '^[0-9a-f]{64}$'),
    validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    FOREIGN KEY (rubric_id, rubric_version)
        REFERENCES rubric_versions(rubric_id, rubric_version),
    CONSTRAINT model_runs_errors_array CHECK (jsonb_typeof(validation_errors) = 'array')
);

CREATE TABLE assessments (
    assessment_id TEXT PRIMARY KEY,
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(paper_version_id),
    assessment_type TEXT NOT NULL
        CHECK (assessment_type IN ('ai', 'human', 'adjudicated')),
    annotator_id TEXT,
    model_run_id TEXT REFERENCES model_runs(model_run_id),
    schema_version TEXT NOT NULL,
    rubric_id TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'completed', 'approved')),
    stage TEXT,
    paper_summary TEXT,
    overall_score NUMERIC(3,1) CHECK (overall_score IS NULL OR overall_score BETWEEN 1 AND 10),
    overall_rating TEXT CHECK (overall_rating IN ('strong', 'good', 'adequate', 'weak', 'unable_to_assess')),
    reading_recommendation TEXT CHECK (reading_recommendation IN (
        'worth_deep_reading', 'worth_reading_with_checks', 'selective_reading',
        'insufficient_evidence', 'unable_to_assess'
    )),
    confidence TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    is_provisional BOOLEAN NOT NULL DEFAULT true,
    provisional_reason TEXT,
    overall_assessment TEXT,
    strengths JSONB NOT NULL DEFAULT '[]'::jsonb,
    concerns JSONB NOT NULL DEFAULT '[]'::jsonb,
    unresolved_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
    limitations_of_assessment JSONB NOT NULL DEFAULT '[]'::jsonb,
    guardrails JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    FOREIGN KEY (rubric_id, rubric_version)
        REFERENCES rubric_versions(rubric_id, rubric_version),
    CHECK (
        (assessment_type = 'ai' AND model_run_id IS NOT NULL)
        OR (assessment_type IN ('human', 'adjudicated') AND model_run_id IS NULL)
    )
);

CREATE TABLE dimension_assessments (
    dimension_assessment_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id) ON DELETE CASCADE,
    dimension_id TEXT NOT NULL CHECK (dimension_id IN (
        'research_value', 'soundness_evidence', 'originality_positioning',
        'clarity_usability', 'reproducibility_transparency'
    )),
    applicability TEXT NOT NULL CHECK (applicability IN ('applicable', 'uncertain', 'not_applicable')),
    score INTEGER CHECK (score IS NULL OR score BETWEEN 1 AND 10),
    rating TEXT NOT NULL CHECK (rating IN ('strong', 'good', 'adequate', 'weak', 'uncertain', 'not_applicable')),
    -- A dimension explicitly marked uncertain/not_applicable has no effective
    -- contribution to the overall score and therefore legitimately uses 0.
    weight NUMERIC(4,3) NOT NULL CHECK (weight >= 0 AND weight <= 1),
    dimension_question TEXT NOT NULL,
    summary TEXT NOT NULL,
    strengths JSONB NOT NULL DEFAULT '[]'::jsonb,
    weaknesses JSONB NOT NULL DEFAULT '[]'::jsonb,
    rationale TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    uncertainty_reason TEXT,
    UNIQUE (assessment_id, dimension_id),
    CHECK (
        (applicability = 'applicable' AND score IS NOT NULL AND uncertainty_reason IS NULL)
        OR (applicability = 'uncertain' AND score IS NULL AND rating = 'uncertain' AND length(btrim(uncertainty_reason)) > 0)
        OR (applicability = 'not_applicable' AND score IS NULL AND rating = 'not_applicable' AND uncertainty_reason IS NULL)
    )
);

CREATE TABLE evidence_references (
    evidence_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id) ON DELETE CASCADE,
    chunk_id TEXT REFERENCES paper_text_chunks(chunk_id) ON DELETE SET NULL,
    element_id TEXT REFERENCES paper_elements(element_id) ON DELETE SET NULL,
    page_number INTEGER CHECK (page_number IS NULL OR page_number > 0),
    section_id TEXT REFERENCES paper_sections(section_id) ON DELETE SET NULL,
    section_title TEXT,
    locator_type TEXT NOT NULL CHECK (locator_type IN ('text', 'figure', 'table', 'equation', 'theorem')),
    locator_label TEXT,
    evidence_mode TEXT NOT NULL CHECK (evidence_mode IN ('quote', 'paraphrase')),
    excerpt TEXT NOT NULL CHECK (length(btrim(excerpt)) > 0),
    location_confidence TEXT NOT NULL CHECK (location_confidence IN ('high', 'medium', 'low')),
    location_caveat TEXT,
    is_absence_evidence BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE assessment_evidence_links (
    evidence_link_id TEXT PRIMARY KEY,
    dimension_assessment_id TEXT NOT NULL
        REFERENCES dimension_assessments(dimension_assessment_id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidence_references(evidence_id) ON DELETE CASCADE,
    stance TEXT NOT NULL CHECK (stance IN ('supports', 'contradicts', 'context')),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) > 0),
    UNIQUE (dimension_assessment_id, evidence_id, stance)
);

CREATE TABLE reproducibility_checks (
    reproducibility_check_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id) ON DELETE CASCADE,
    check_id TEXT NOT NULL CHECK (check_id IN (
        'code_availability', 'data_availability', 'random_seed',
        'hyperparameters', 'hardware_environment', 'preprocessing_details'
    )),
    status TEXT NOT NULL CHECK (status IN (
        'available', 'not_available', 'unable_to_verify', 'not_applicable'
    )),
    detail TEXT NOT NULL,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (assessment_id, check_id)
);

CREATE TABLE claimed_contributions (
    contribution_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id) ON DELETE CASCADE,
    contribution_text TEXT NOT NULL,
    contribution_type TEXT NOT NULL,
    evidence_id TEXT REFERENCES evidence_references(evidence_id) ON DELETE SET NULL
);

CREATE TABLE claim_checks (
    claim_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id) ON DELETE CASCADE,
    claim_text TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    scope TEXT,
    verdict TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE benchmark_memberships (
    dataset_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(paper_version_id),
    split TEXT NOT NULL CHECK (split IN ('calibration', 'validation', 'test')),
    sample_quality_band TEXT CHECK (sample_quality_band IN (
        'high_consensus', 'mixed_or_borderline', 'substantial_concerns'
    )),
    decision TEXT,
    decision_verified BOOLEAN NOT NULL DEFAULT false,
    ai_assessment_id TEXT REFERENCES assessments(assessment_id),
    gold_assessment_id TEXT REFERENCES assessments(assessment_id),
    annotation_status TEXT NOT NULL CHECK (annotation_status IN (
        'selected', 'parsed', 'ai_provisional', 'double_annotated',
        'adjudicated', 'approved', 'excluded'
    )),
    PRIMARY KEY (dataset_id, dataset_version, paper_version_id)
);

CREATE TABLE dataset_releases (
    dataset_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    rubric_id TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('provisional', 'released', 'retired')),
    gold_status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, dataset_version)
);

-- Backend-facing visual locator. Git stores metadata rather than duplicating
-- hundreds of rendered pages. source_page_url works immediately; the exact
-- asset_object_key can be materialized with scripts/materialize_page_assets.py.
CREATE VIEW paper_element_assets AS
SELECT
    e.element_id,
    e.paper_version_id,
    e.element_type,
    e.label,
    e.caption,
    e.page_number,
    v.source_url AS source_pdf_url,
    CASE
        WHEN v.source_url IS NOT NULL AND e.page_number IS NOT NULL
        THEN v.source_url || '#page=' || e.page_number::text
    END AS source_page_url,
    e.asset_object_key,
    CASE
        WHEN e.asset_object_key IS NULL THEN 'not_applicable'
        ELSE 'generate_on_demand'
    END AS asset_delivery_status
FROM paper_elements e
JOIN paper_versions v USING (paper_version_id);

COMMIT;
