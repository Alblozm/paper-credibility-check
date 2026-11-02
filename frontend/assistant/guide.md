# PaperTrace user guide (assistant knowledge)

PaperTrace assesses the credibility of machine learning papers. For one paper it runs three checks and reports a
provisional trial score. Optionally it also produces a five-dimension quality assessment in parallel.

The interface and all assistant answers use English. Answer in English even when a question, page context or
earlier conversation uses another language. User questions and personal notes can retain their original wording.

## What the assessment does and does not do

- Claims and evidence: each principal claim is compared with the passages in the paper meant to support it.
- References: each bibliographic entry is matched against external records (arXiv, publishers, Crossref and others);
  entries that cannot be matched are reported with the reason.
- Code and reproduction: the paper and the web are searched for official code and independent reproduction reports.
- It does not run code, does not judge suitability for publication and is not a peer review.
- Scores are provisional under rubric 0.2-provisional-complete; they are trial scores, not approved assessments.

## Starting an assessment (start page, "New assessment")

1. Choose the source: arXiv (identifier such as 2310.01798 or an arxiv.org URL) or PDF (drag a file in or select it;
   at most 30 MB and 120 pages; English machine learning papers only).
2. Choose a model in the Model menu. The line under the form describes the selected model.
   - GPT: balanced reasoning with built-in web search; the default.
   - Claude: careful reading of long papers with precise quotation.
   - Gemini: very long context and fast responses; it reasons before answering.
   Claude and Gemini use the GPT search service for the code search; every model call is recorded in the report.
3. Keep "Include a five-dimension quality assessment" ticked to run the quality assessment in parallel.
4. Select "Start assessment".

## While it runs (waiting page)

- A credibility assessment usually takes 5 to 15 minutes; the quality assessment about one minute.
- The dial shows overall progress; below it the current stage, what each check is doing and an estimated time range.
- Each check (claims, references, code, and the quality assessment) shows its own progress.
- The quality assessment card below explains what it is. While it runs its button reads "Opens when ready" with the
  progress; when it is ready it shows the score and a "Read the quality assessment" button. "While you wait" lists
  other finished assessments.
- You can leave the page at any time: every assessment keeps a tab in the title bar, with a progress pie while it runs.
  Tabs show the model's logo; two tabs for the same paper also show their start time. Closing a tab cancels nothing;
  closing the tab you are viewing opens the tab next to it.
- To stop one check that runs far longer than the others (often the code check), select the small square button at
  the end of its line and confirm with "Stop check". It ends within about a second, is recorded as stopped by you and
  receives the fallback score of 3, never a finding; the other checks continue and the report is produced as usual.
- To stop the whole assessment, select "Cancel assessment" under the dial and confirm with "Stop". It is kept in the
  history as "Cancelled" and can be retried; nothing is scored.

## Reading the report

The bar at the top of a run's page has two sliding switches. The first picks the part: **Credibility** or
**Quality**; each shows its own state (score, progress, Failed, Cancelled or Not run). The second picks how the
credibility report is read: **Paper** or **Report**. In the Paper view the reader's tools sit in the same bar, after
the switches; the model, start time and duration are in the status bar at the bottom.

Quality shows the five-dimension report as a document, so the Paper/Report switch folds away there. If the quality
assessment failed, "Retry quality assessment" starts it again; if it was not run, "Run quality assessment" starts one
for the same paper.

The Credibility part has two views:

- Paper: the PDF in the middle with evidence highlighted in place. The left panel has Outline (sections with evidence),
  Pages (thumbnails) and Skills (highlight layers that can be switched off). The right side holds widgets:
  Verdict, Claims, References, Code, Evidence, Process record and Notes. The icon rail on the far
  right jumps to a widget; widgets can be collapsed, reordered by dragging the grip, closed and added back with "+".
  Select a highlight to see the claim or reference behind it, copy the quotation, or mark it reviewed.
  "Search the paper" finds text in the PDF.
- Report: the credibility assessment as a readable document with a section outline on the left, ending with run
  details (the model with its version, and the start and finish times).

## Scores and categories

- The trial score is the unweighted mean of the three check scores (each 0 to 10).
- Categories by score: 8 to 10 "No significant concerns found"; 6 to 8 "Some concerns"; 3 to 6 "Significant
  concerns"; 0 to 3 "Critical concerns". The unrounded score decides the category.
- Concerns identified: problems the checks actually found (for example a claim the paper contradicts).
- Verification gaps: items that could not be checked or confirmed (a reference whose source was unavailable,
  code that could not be found). A gap is not an error in the paper.
- Fallback score: when a check cannot finish (for example a model timeout or an incomplete scope), that check
  receives a fixed fallback score of 3 instead of a measured one. A report with fallback scores says little about the
  paper in that dimension; the Process record widget shows what happened.
- Claim outcomes: Supported, Partially supported, Contradicted, Not supported in scope (no supporting passage was
  found in the paper), Undetermined, Not checked.
- Reference outcomes: Matched, Metadata conflict, Not verified, Not checked. Reasons include "Source unavailable",
  "No record found in scope", "Author mismatch" and "Insufficient metadata".

## Five-dimension quality assessment

- It rates research value, soundness and evidence, originality and positioning, clarity and usability, and
  reproducibility and transparency, each 1 to 10, and gives a reading recommendation.
- Rating bands: below 5.5 Weak, 5.5 to 7 Adequate, 7 to 8.5 Good, 8.5 and above Strong.
- It is independent of the credibility score: it judges scholarly quality, not verifiability.
- A dimension without enough evidence is "Not scored"; the overall score then covers the remaining dimensions.

## Export, links and history

- Export (title bar on a report): "Report (PDF)" opens a printable report with both parts and the run details; choose
  "Save as PDF" in the print dialog. "Raw data (JSON)" downloads both assessments of the run as recorded.
- Copy link copies the report's address; every report has its own address and can be reopened at any time.
- History has one row per run: paper, model, the credibility result, the quality result and the start time. Each part
  shows its own state; a failed or missing quality assessment can be retried or run from the row. Runs of the same
  paper stay separate. Opening a row does not re-run anything.
- A run that is still running appears at the top with "Running" and its progress; its quality assessment stays in
  the same row.
- Delete a run with the bin button at the end of its row, then "Delete". Both assessments of the run are removed
  from the server and cannot be restored. A run that is still running cannot be deleted; cancel it first.
- An uploaded PDF is kept in this browser, so its report shows the paper after a reload. In another browser the
  report lists the evidence passages instead; selecting the same PDF file shows it again.

## When an assessment fails

- The failure page names the problem and, when it can be retried, offers "Retry assessment".
- Common causes: the arXiv identifier does not exist; the PDF is password protected, too large or has too many pages;
  the paper is outside the scope (not an English machine learning paper; shown neutrally, not as an error); the
  analysis model was unavailable or timed out (retry later or choose another model).

## Privacy and data

- Uploaded PDFs are processed on the PaperTrace server for the assessment. Notes and reviewed marks are stored only in the
  browser.
- The assistant sees a short summary of the page you are on (scores and counts, never the paper or your notes); the
  line above its input says what is shared. Conversations stay in the browser tab. When you rate an answer as helpful
  or not, that question and answer are kept on the server to improve this guide.
