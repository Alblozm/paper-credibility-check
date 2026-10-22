(function () {
  var evidence = [
    {
      evidence_id: "ev_01",
      page_number: 1,
      section_id: "sec_01",
      section_title: "Abstract",
      locator_type: "text",
      locator_label: null,
      evidence_mode: "quote",
      excerpt: "We introduce a retrieval-aware evaluation framework that links model judgments to verifiable passages in the source document.",
      location_confidence: "high"
    },
    {
      evidence_id: "ev_03",
      page_number: 5,
      section_id: "sec_03",
      section_title: "3. Method",
      locator_type: "equation",
      locator_label: "Equation 2",
      evidence_mode: "paraphrase",
      excerpt: "The scoring function combines claim relevance, passage support and calibration into a single evidence selection objective.",
      location_confidence: "high"
    },
    {
      evidence_id: "ev_04",
      page_number: 7,
      section_id: "sec_04",
      section_title: "4.2 Main Results",
      locator_type: "table",
      locator_label: "Table 2",
      evidence_mode: "quote",
      excerpt: "Our method improves evidence precision across all three benchmark datasets while maintaining comparable recall.",
      location_confidence: "high"
    },
    {
      evidence_id: "ev_05",
      page_number: 8,
      section_id: "sec_04",
      section_title: "4.3 Baseline Setup",
      locator_type: "text",
      locator_label: null,
      evidence_mode: "paraphrase",
      excerpt: "Two baselines use reported results from prior work rather than reruns under the same compute and retrieval settings.",
      location_confidence: "medium"
    },
    {
      evidence_id: "ev_06",
      page_number: 9,
      section_id: "sec_05",
      section_title: "5. Ablation Study",
      locator_type: "table",
      locator_label: "Table 4",
      evidence_mode: "quote",
      excerpt: "Removing passage calibration reduces evidence precision by 6.2 points on average.",
      location_confidence: "high"
    },
    {
      evidence_id: "ev_08",
      page_number: 11,
      section_id: "sec_06",
      section_title: "6. Limitations",
      locator_type: "text",
      locator_label: null,
      evidence_mode: "quote",
      excerpt: "We did not normalize training and retrieval budgets for every external baseline.",
      location_confidence: "high"
    },
    {
      evidence_id: "ev_11",
      page_number: 12,
      section_id: "sec_07",
      section_title: "Appendix A. Implementation Details",
      locator_type: "text",
      locator_label: null,
      evidence_mode: "paraphrase",
      excerpt: "The appendix lists main hyperparameters and hardware but does not report random seeds.",
      location_confidence: "medium"
    }
  ];

  function link(id, stance, reason) {
    return { evidence_id: id, stance: stance, reason: reason };
  }

  var dimensions = [
    {
      dimension_id: "research_value",
      display_name: "Research Value & Significance",
      applicability: "applicable",
      score: 8,
      rating: "good",
      weight: 0.2,
      summary: "The paper targets traceability in paper assessment, a problem the community currently handles informally, and states the goal precisely enough to be tested.",
      confidence: "high",
      uncertainty_reason: null,
      evidence_links: [link("ev_01", "supports", "The abstract states the traceable-judgment goal directly.")]
    },
    {
      dimension_id: "soundness_evidence",
      display_name: "Soundness & Evidence Support",
      applicability: "applicable",
      score: 8,
      rating: "good",
      weight: 0.2,
      summary: "Main results and ablations support the core design, but two baselines were not rerun under the paper's own compute and retrieval settings.",
      confidence: "medium",
      uncertainty_reason: null,
      evidence_links: [
        link("ev_04", "supports", "Table 2 reports precision gains on all three datasets."),
        link("ev_05", "context", "Some baselines reuse published numbers, so the comparison may not be like-for-like.")
      ]
    },
    {
      dimension_id: "originality_positioning",
      display_name: "Originality & Positioning",
      applicability: "applicable",
      score: 7,
      rating: "good",
      weight: 0.2,
      summary: "Pairing evidence calibration with paper assessment is a genuine step, though the delta against existing retrieval-grounded evaluation could be drawn more sharply.",
      confidence: "medium",
      uncertainty_reason: null,
      evidence_links: [link("ev_03", "supports", "The method section defines an objective combining relevance, support and calibration.")]
    },
    {
      dimension_id: "clarity_usability",
      display_name: "Clarity & Usability",
      applicability: "applicable",
      score: 8,
      rating: "good",
      weight: 0.2,
      summary: "Problem, method and evaluation protocol are laid out in order, and the main result tables are easy to locate and read.",
      confidence: "high",
      uncertainty_reason: null,
      evidence_links: [link("ev_04", "supports", "Main results present datasets, baselines and key metrics in one place.")]
    },
    {
      dimension_id: "reproducibility_transparency",
      display_name: "Reproducibility & Transparency",
      applicability: "applicable",
      score: 7,
      rating: "good",
      weight: 0.2,
      summary: "Key hyperparameters and hardware are reported, but random seeds are absent and some comparison budgets are incomplete.",
      confidence: "medium",
      uncertainty_reason: null,
      evidence_links: [
        link("ev_11", "supports", "The appendix gives the main implementation details."),
        link("ev_08", "context", "The limitations section admits budgets were not normalized across baselines.")
      ]
    }
  ];

  var report = {
    paper_summary: "The paper proposes an evidence-calibrated framework for assessing research papers. Model-generated judgments are bound to verifiable passages inside the paper itself, and the framework is evaluated on three benchmarks for evidence-localisation precision and recall.",
    claimed_contributions: [
      {
        contribution_id: "contrib_01",
        statement: "A retrieval-calibrated framework that binds paper-assessment judgments to passages in the source document.",
        contribution_type: "method",
        evidence_links: [link("ev_01", "supports", "The abstract describes this methodological contribution.")]
      }
    ],
    claim_checks: [
      {
        claim_id: "claim_01",
        claim: "The method improves evidence-localisation precision on three datasets.",
        support_level: "partially_supported",
        reasoning: "The main results support an overall improvement, but not every baseline was rerun under identical settings, so part of the margin is not directly attributable to the method.",
        confidence: "medium",
        evidence_links: [
          link("ev_04", "supports", "Table 2 reports precision gains across the three datasets."),
          link("ev_05", "context", "The baseline setup notes that some numbers come from prior publications.")
        ]
      }
    ],
    dimensions: dimensions,
    reproducibility_checklist: [
      { check_id: "code_availability", display_name: "Code availability", status: "available", detail: "A public repository link is given in the appendix.", evidence_links: [link("ev_11", "supports", "The appendix includes the repository URL.")] },
      { check_id: "data_availability", display_name: "Data availability", status: "available", detail: "All three evaluation datasets are public.", evidence_links: [link("ev_04", "supports", "Main results list the public datasets used.")] },
      { check_id: "random_seed", display_name: "Random seed", status: "not_available", detail: "No random seed is reported in the body or the appendix.", evidence_links: [link("ev_11", "supports", "Implementation details contain no seed.")] },
      { check_id: "hyperparameters", display_name: "Hyperparameters", status: "available", detail: "The appendix reports the main retrieval and training parameters.", evidence_links: [link("ev_11", "supports", "The appendix lists the key parameters.")] },
      { check_id: "hardware_environment", display_name: "Hardware environment", status: "available", detail: "Training hardware is described in the appendix.", evidence_links: [link("ev_11", "supports", "The appendix names the accelerators used.")] },
      { check_id: "preprocessing_details", display_name: "Preprocessing details", status: "unable_to_verify", detail: "Some preprocessing steps are described too briefly to verify.", evidence_links: [link("ev_11", "context", "The appendix gives only part of the preprocessing configuration.")] }
    ],
    strengths: [
      { finding_id: "finding_01", title: "The evidence chain is explicit", detail: "Judgment, evidence location and the reason for the link are kept as separate fields, so a reader can check any single claim without re-reading the paper.", importance: "major", confidence: "high", evidence_links: [link("ev_01", "supports", "The abstract states the binding between judgment and source passage.")] },
      { finding_id: "finding_02", title: "Experimental coverage is reasonably complete", detail: "Three datasets, the standard baselines and an ablation over the key components are all present.", importance: "major", confidence: "high", evidence_links: [link("ev_06", "supports", "The ablation isolates the contribution of passage calibration.")] }
    ],
    concerns: [
      { finding_id: "finding_03", title: "Comparison conditions are not uniform", detail: "Some baselines reuse published results; training and retrieval budgets were not normalised, so part of the reported margin may come from the setup rather than the method.", importance: "major", confidence: "medium", evidence_links: [link("ev_05", "supports", "The baseline setup states that not all baselines were rerun.")] },
      { finding_id: "finding_04", title: "Randomness is under-reported", detail: "No seeds are given, so variance across runs and the stability of the reported gains cannot be judged.", importance: "minor", confidence: "medium", evidence_links: [link("ev_11", "supports", "Implementation details omit the random seed.")] }
    ],
    unresolved_questions: [
      { question_id: "question_01", question: "Do the reported gains hold once training and retrieval budgets are equalised across baselines?", why_it_matters: "It determines whether the headline comparison is a fair one.", evidence_links: [link("ev_08", "context", "The paper acknowledges that budgets were not normalised.")] }
    ],
    overall_assessment: {
      overall_score: 7.6,
      rating: "good",
      summary: "A useful and clearly written approach to evidence-grounded assessment. The core method is supported by the paper's own results, but baseline comparability and reproducibility reporting both need checking before the numbers are quoted.",
      reading_recommendation: "worth_reading_with_checks",
      recommendation_reason: "The method and main results are backed by in-paper evidence, so the paper is worth your time — but read the baseline setup and the missing seed information before relying on the margins.",
      confidence: "medium",
      is_provisional: false,
      calculation: { method: "weighted_average", assessed_dimension_count: 5, excluded_dimension_ids: [] }
    },
    evidence: evidence,
    limitations: [
      "This report only analyses information present in the paper. No code was run and no experiment was reproduced.",
      "No external leaderboard was consulted to verify claims of state-of-the-art results.",
      "Training and retrieval budgets for some baselines could not be confirmed from the paper alone."
    ],
    generated_at: "2026-08-28T06:30:00Z"
  };

  var completed = {
    schema_version: "0.4",
    assessment_id: "asmt_demo_01",
    status: "completed",
    stage: "report_ready",
    progress_percent: 100,
    source: { type: "arxiv", arxiv_id: "2501.01234", original_url: "https://arxiv.org/abs/2501.01234", upload_id: null, original_filename: null },
    venue: { venue_id: "iclr_2027_main", code: "ICLR", name: "International Conference on Learning Representations", year: 2027, track: "main", rubric_id: "iclr_inspired_due_diligence", rubric_version: "0.1", is_available: true },
    model: { model_id: "model_standard_v1", display_name: "Standard analysis", provider: "internal", model_version: "2026-08-25", description: "Balances analysis depth against turnaround time.", speed_tier: "standard", is_available: true, is_default: true },
    paper: { paper_id: "paper_demo_01", title: "Evidence-Calibrated Assessment for Research Papers", authors: ["Lin Chen", "Maya Patel", "Alex Kim"], abstract: "A framework for linking research-paper assessments to verifiable evidence.", language: "en", page_count: 14, arxiv_id: "2501.01234", doi: null, sections: [], parse_quality: "good", parse_warnings: [] },
    report: report,
    warnings: [
      { code: "PAGE_NUMBER_UNCERTAIN", message: "Page numbers for some appendix evidence may be off by one.", stage: "linking_evidence" }
    ],
    error: null,
    created_at: "2026-08-28T06:27:00Z",
    started_at: "2026-08-28T06:27:03Z",
    completed_at: "2026-08-28T06:30:00Z"
  };

  var provisional = JSON.parse(JSON.stringify(completed));
  provisional.assessment_id = "asmt_demo_02";
  provisional.report.dimensions[2].applicability = "uncertain";
  provisional.report.dimensions[2].score = null;
  provisional.report.dimensions[2].rating = "uncertain";
  provisional.report.dimensions[2].summary = "The related-work section was only partially parsed, so the paper's positioning against prior work cannot be judged reliably.";
  provisional.report.dimensions[2].confidence = "low";
  provisional.report.dimensions[2].uncertainty_reason = "Parse quality was poor on the related-work pages.";
  provisional.report.dimensions[2].evidence_links = [];
  provisional.report.overall_assessment.overall_score = 7.8;
  provisional.report.overall_assessment.is_provisional = true;
  provisional.report.overall_assessment.calculation.assessed_dimension_count = 4;
  provisional.report.overall_assessment.calculation.excluded_dimension_ids = ["originality_positioning"];
  provisional.report.overall_assessment.summary = "The four assessable dimensions suggest the paper is worth reading, but its originality and positioning could not be judged from the parsed text.";
  provisional.warnings.push({ code: "SECTION_PARSE_PARTIAL", message: "Related Work was parsed incompletely, so the originality dimension is unscored.", stage: "parsing_paper" });

  window.PCC_MOCK = {
    venues: [
      { venue_id: "iclr_2027_main", code: "ICLR", name: "International Conference on Learning Representations", year: 2027, track: "main", rubric_id: "iclr_inspired_due_diligence", rubric_version: "0.1", is_available: true }
    ],
    models: [
      { model_id: "model_standard_v1", display_name: "DeepSeek Review Model", provider: "deepseek", model_version: "deepseek-v4-flash", description: "Balanced analysis quality and processing time.", speed_tier: "standard", is_available: true, is_default: true },
      { model_id: "model_gpt_v1", display_name: "GPT Review Model", provider: "openai", model_version: "gpt-4.1-mini", description: "OpenAI GPT analysis through the Responses API.", speed_tier: "standard", is_available: true, is_default: false }
    ],
    stages: [
      { stage: "validating_input", label: "Validate input", description: "Checking the paper source, rubric and model selection.", progress: 8 },
      { stage: "retrieving_paper", label: "Retrieve paper", description: "Fetching the paper file and its public metadata.", progress: 22 },
      { stage: "parsing_paper", label: "Parse paper", description: "Extracting body text, sections and paper metadata.", progress: 38 },
      { stage: "analyzing", label: "Analyse", description: "Scoring the paper across the five rubric dimensions.", progress: 58 },
      { stage: "linking_evidence", label: "Link evidence", description: "Anchoring each judgement to pages, sections, tables and figures.", progress: 76 },
      { stage: "finalizing_report", label: "Assemble report", description: "Validating the structure and settling the reading recommendation.", progress: 92 },
      { stage: "report_ready", label: "Report ready", description: "The report has passed structural validation.", progress: 100 }
    ],
    errors: {
      retryable: { code: "MODEL_TIMEOUT", message: "The analysis model did not respond in time. Nothing was charged and the assessment can be run again.", stage: "analyzing", retryable: true, debug_id: "dbg_demo_01" },
      nonRetryable: { code: "PDF_PARSE_FAILED", message: "No readable body text could be extracted from this PDF. Scanned documents are not OCR-ed.", stage: "parsing_paper", retryable: false, debug_id: "dbg_demo_02" }
    },
    completed: completed,
    provisional: provisional
  };
})();
