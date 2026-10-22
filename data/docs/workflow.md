# Dataset workflow

## Responsibility boundary

The dataset covers sample selection, source provenance, the structuring contract,
rubric detail, the released AI-authoritative reference scores, evidence checks
and quality acceptance. Human annotation and adjudication are optional future
research extensions, not release gates for dataset 0.6.0-ai-final.

The backend covers online PDF retrieval, object storage, automatic parsing,
text chunking, database writes, model calls, retries and API delivery. The dataset
defines and checks the expected parser output.

## Paper lifecycle

Use one of the following values in the manifest:

1. `candidate`
2. `selected`
3. `sources_verified`
4. `parsed`
5. `annotation_in_progress`
6. `approved`
7. `excluded`

The schema retains `double_annotated` and `adjudicated` for optional future
human-agreement studies; the current release moves from AI assessment and
evidence validation directly to `approved`.

## Pilot procedure

1. Select three papers that cover different evaluation behaviours.
2. Verify the OpenReview forum, paper version, public reviews and decision.
3. Record every source URL and retrieval date.
4. Ask the backend parser to produce metadata, sections, chunks and elements.
5. Check title, abstract, reading order, section boundaries, page locations and
   table/figure captions.
6. Generate an AI reference assessment under the locked rubric.
7. Check every dimension score and its supporting/limiting evidence links.
8. Mark a paper `approved` only after schema, evidence and release checks pass.

## Versioning

Every approved result records `schema_version`, `rubric_version`,
`dataset_version`, `paper_version` and source retrieval dates. Corrections do
not silently overwrite a released benchmark; they create a new dataset version.
