# AI scoring guide v0.1

## Scoring protocol

The AI receives the locked rubric, structured paper content and relevant
paper-internal evidence. Public reviews and accept/reject decisions are excluded
from the scoring context; they are used later to evaluate the AI result.

For every dimension the AI must:

1. state the dimension-specific question;
2. identify relevant claims and evidence;
3. evaluate both strengths and limitations;
4. select `applicable`, `uncertain`, or `not_applicable`;
5. return an integer 1-10 only when applicable;
6. provide a concise rationale, confidence and evidence links;
7. avoid claims not supported by the paper.

## Common score anchors

| Score | Interpretation |
|---:|---|
| 9-10 | Exceptional on this dimension; direct, broad and convincing evidence with only minor limitations |
| 7-8 | Good; the core requirement is met with credible evidence, but bounded weaknesses remain |
| 5-6 | Adequate; partially convincing with material limitations or incomplete support |
| 3-4 | Weak; important requirements are poorly met or major evidence gaps affect the core claims |
| 1-2 | Very weak; the dimension substantially fails for central claims or is contradicted by the paper's own evidence |

Scores are holistic within a dimension. Feature checks guide reasoning but are
not mechanically averaged. A critical failure affecting the central claim can
cap a score even if several minor checks pass.

## 1. Research Value & Significance

### Features to inspect

- specificity and importance of the research problem;
- strength of motivation and documented gap;
- relevance to researchers or practitioners;
- potential knowledge, methodological, data or negative-result value;
- likely impact if the claims hold;
- whether significance is argued without relying only on SOTA performance.

### Anchors

- `9-10`: important problem, compelling gap and potentially broad or deep value;
- `7-8`: clearly useful contribution with credible significance, but narrower impact;
- `5-6`: reasonable motivation and some value, though incremental or weakly justified;
- `3-4`: unclear need, limited value or significance claims exceeding support;
- `1-2`: no coherent research value for the stated work.

Primary evidence: abstract, introduction, related work, results, discussion and
conclusion.

## 2. Soundness & Evidence Support

### Features to inspect

- clarity and testability of major claims;
- appropriateness of method/theory for the stated problem;
- match between claims and experiments or proofs;
- baseline relevance and fairness;
- dataset, metric and evaluation-protocol suitability;
- ablations, sensitivity checks and robustness evidence where needed;
- reporting of variance, repeated runs, confidence intervals or significance
  tests when variability matters;
- overclaiming, unsupported causal language and internal contradictions.

### Anchors

- `9-10`: central claims are comprehensively supported with rigorous design and no material gap;
- `7-8`: central claims are supported, with limited missing checks or comparison weaknesses;
- `5-6`: useful evidence exists, but one or more material gaps limit confidence;
- `3-4`: central claims rely on weak/incomplete comparisons, design flaws or substantial overclaiming;
- `1-2`: evidence does not support the central claims or contains fundamental contradictions.

Primary evidence: method, theory, experiments, result tables, ablations,
appendices and limitations.

## 3. Originality & Positioning

### Features to inspect

- explicit statement of the novel contribution;
- distinction from cited prior work;
- meaningfulness of the difference, not merely new terminology;
- whether novelty comes from method, problem, data, analysis, theory or a useful combination;
- acknowledgement of closely related work and limitations of novelty claims.

### Anchors

- `9-10`: clearly differentiated and potentially field-shaping contribution;
- `7-8`: meaningful novelty with good literature positioning;
- `5-6`: incremental but identifiable contribution, or incomplete positioning;
- `3-4`: novelty is vague, minor or poorly distinguished from cited work;
- `1-2`: claimed originality is unsupported or contradicted by the paper's own related-work account.

The system does not perform exhaustive prior-art search and therefore evaluates
the paper's positioning, not the global fact that nobody has done it before.

## 4. Clarity & Usability

### Features to inspect

- clear problem, contribution and claim statements;
- logical organisation and consistent terminology;
- sufficient method detail for understanding;
- legibility and explanation of equations, figures and tables;
- consistency between text, tables and conclusions;
- clear assumptions, limitations and intended use.

### Anchors

- `9-10`: precise, coherent and easy to follow; artifacts strongly aid understanding;
- `7-8`: clear overall with limited local ambiguity or organisation issues;
- `5-6`: understandable with effort, but important explanations or connections are weak;
- `3-4`: recurring ambiguity or disorganisation impedes verification and use;
- `1-2`: central problem, method or results cannot be reliably understood.

Do not punish technical complexity by itself; assess whether the paper explains
the complexity appropriately for its audience.

## 5. Reproducibility & Transparency

### Features to inspect

- code availability;
- data availability or precise dataset identification;
- random seeds/repeated-run details where applicable;
- hyperparameters and training/evaluation settings;
- hardware and software/environment details;
- preprocessing details;
- clear assumptions, limitations and resource requirements.

### Anchors

- `9-10`: nearly complete and directly usable disclosure for the paper type;
- `7-8`: most necessary information is available, with limited omissions;
- `5-6`: partial reproducibility information with material but manageable gaps;
- `3-4`: several important details are absent, making reproduction difficult;
- `1-2`: central procedures and resources are largely undisclosed.

The six checklist statuses inform the score but are not mechanically counted.
Use `not_applicable` for genuinely irrelevant items and explain why. Absence of
code alone cannot force a low overall paper score.

## Uncertain and not-applicable rules

Return `uncertain` with `score: null` when parsing is incomplete, key evidence
cannot be located, or the available paper version does not support a reliable
judgment. Return `not_applicable` only when the dimension or check genuinely does
not apply to the paper type. Never encode either state as zero.

## Overall result

The backend computes the normalised equal-weight mean over applicable numeric
dimensions and returns one decimal place. With fewer than three numeric
dimensions, `overall_score` is null and the result is `unable_to_assess`.

The AI must never output accept/reject probability or claim to have reproduced
the experiments.
