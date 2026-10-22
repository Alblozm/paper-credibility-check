# Frontend

**English** · [中文](README.zh-CN.md)

React + TypeScript + Vite web app for Paper Credibility Check. The interface is in English. Text returned by the backend, such as `message` and `summary`, is shown as is.

## Pages

One top bar with three sections:

- **Credibility check** (home, `/#check`; the old `/#credibility` still works): submit an arXiv ID, link or PDF. You can also tick "five-dimension scoring" to run a quick score alongside. Example reports from completed checks are listed below the form.
- **While a check runs**: the page explains the expected wait. Progress stays at 60% until all three checks finish. You can read an example report or the quick score meanwhile. Polling continues when you switch sections, and a progress pill in the top bar leads back.
- **Report**: overall score and the three checks (claims & evidence, references, code & reproduction). Evidence opens in a side drawer. **How this check ran** shows each check's duration and status, the model steps, how many references the agent verified, and the code search trail. The backend sends this trace with the finished report; it is not streamed live.
- **Paper scoring**: the five-dimension flow with its landing page, progress view and report.
- **Reports**: every saved credibility check and five-dimension score, with type filter and search. Opening a saved report does not call a model again.

## Run

Node.js 20.19+ or 22.12+ and npm 10+.

```bash
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

The dev server forwards `/v1` to `http://127.0.0.1:8000`, so start the backend first (see [backend/README.md](../backend/README.md)). In HTTP mode a failed request shows an error and never falls back to mock data.

If the model menu is empty, check `GET /v1/models` on the backend. `is_available: false` usually means the backend did not read an API key. Fix `.env`, restart the backend and reload the page.

### Mock mode (no backend)

```bash
VITE_API_MODE=mock npm run dev -- --host 127.0.0.1 --port 5174
```

Mock mode uploads nothing and calls no model, parser or external service.

- **Paper scoring** lets you pick one of four outcomes: full report, provisional report with an `uncertain` dimension, retryable model timeout, and non-retryable PDF parse failure. The mock API walks through queued, seven processing stages and the result. The data lives in `public/mock-data.js`.
- **Credibility check** offers review scenarios: a prepared first claim (arXiv `2310.01798v1`, real source pages 1 and 5), a core contradiction, an external lookup failure, code not applicable, and a parse failure. Apart from the first claim, these are synthetic and never presented as a parse of the input. External `example.invalid` links are marked as unavailable examples.

Mock mode and the backend's fixture model both label their reports as examples, so sample scores are not mistaken for real results.

## Commands

```bash
npm run dev      # dev server
npm test         # Vitest suite
npm run lint     # oxlint
npm run build    # type check and production build
npm run preview  # preview the production build
```

## Code map

- `src/App.tsx`: top bar, sections and API mode selection (`VITE_API_MODE`).
- `src/types/assessment.ts`, `src/types/credibility.ts`: types for the five-dimension contract v0.4 and the credibility contract `credibility-0.5.0-draft`.
- `src/api/`: one interface per flow, each with an HTTP adapter (`http*Api.ts`) and a mock (`mock*Api.ts`). The HTTP adapters handle uploads, job creation, polling with backoff and history.
- `src/hooks/useAssessment.ts`: create, poll and retry for five-dimension scoring.
- `src/utils/arxiv.ts`: arXiv input normalization, using the same rules as the backend's `app/parsing/arxiv.py`.
- `src/constants/`: labels, error-code titles, the five dimensions and score bands.
- `src/components/`: `CredibilityWorkspace`, `CredibilityReport`, `CredibilityProcess` (how a check ran), `CredibilityEvidenceDrawer`, `ExampleReports`, `ReportsPage`, and the five-dimension `LandingPage`, `InputWorkspace`, `AssessmentProgress`, `AssessmentReport`, `EvidenceDrawer`, `AssessmentErrorView`.
- `src/index.css`: design tokens and page styles; no UI framework.
- `src/test/fixtures/`: captured backend responses used by the tests.

## Display rules

- **Unknown is not zero.** A dimension whose `score` is `null` shows `—` and its reason. The overall score notes how many dimensions it is based on.
- **Every finding leads back to a source.** Evidence chips show page and location (for example `Supports · p. 7 · Table 2`). They open a drawer; `←` / `→` move between evidence for the same finding and `Esc` closes it.
- **Scores come from the backend.** Overall score, rating, categories, evidence relations and the model and venue options are all computed by the backend; the frontend never recomputes them.

## Deployment

The `/v1` proxy only exists in `npm run dev`. A production build or `npm run preview` needs a reverse proxy that forwards `/v1` to the backend. Model API keys belong to the backend only; never put them in the frontend or in a `VITE_` variable.
