# Frontend

**English** · [中文](README.zh-CN.md)

The PaperTrace web app: React 19, TypeScript, Vite, Tailwind CSS 4, TanStack Router and Query, and pdf.js. The interface is in English.

## Pages

- **Start page** (`/`): choose arXiv or PDF, pick a model (GPT, Claude, Gemini or DeepSeek, whichever the backend offers), and keep "Include a five-dimension quality assessment" ticked to score quality in the same run.
- **Waiting page**: a progress dial, the current stage of each check with an estimated time range, and the quality assessment's own progress. You can stop one slow check ("Stop check") or the whole run ("Cancel assessment"). Every run keeps a tab in the title bar, so you can leave and come back.
- **Run page** (`/check/$id`): two switches at the top choose the part (**Credibility** or **Quality**) and, for credibility, the view.
  - **Paper** view: the PDF with evidence highlighted in place. The left panel has Outline, Pages and Skills (highlight layers). Widgets on the right show the Verdict, Claims, References, Code, Evidence, Process record and Notes; they can be collapsed, reordered, closed and added back.
  - **Report** view: the credibility report as a document with a section outline.
  - **Quality**: the five-dimension report. A failed or missing quality assessment can be retried or run from here.
- **History** (`/reports`): one row per run with both results, search, retry and delete. A run that is still running appears at the top.
- **Export**: "Report (PDF)" opens a printable page with both parts (`/check/$id/print`); "Raw data (JSON)" downloads both assessments. Every report has its own address.
- **Help assistant** (optional): a chat window in the corner that answers questions about using PaperTrace.

Notes and "reviewed" marks stay in the browser. An uploaded PDF is kept in the browser's IndexedDB so its report can show the paper after a reload.

## Run

Node.js 20.19+ or 22.12+ and npm 10+.

```bash
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

The dev server forwards `/v1` to the backend at `http://127.0.0.1:8000` (see [backend/README.md](../backend/README.md)) and `/assistant` to the help assistant at `http://127.0.0.1:8020`. A failed request shows an error; it never falls back to sample data.

If the model menu is empty, check `GET /v1/models` on the backend. `is_available: false` usually means the backend did not read that model's API key. Fix `backend/.env`, restart the backend and reload the page.

### Mock mode (no backend)

```bash
VITE_API_MODE=mock npm run dev -- --host 127.0.0.1 --port 5174
```

Mock mode uses the sample data in `public/mock-data.js` and `src/api/mock*Api.ts`. It uploads nothing and calls no model, parser or external service, and it labels its reports as samples.

### Help assistant (optional)

```bash
npm run assistant          # starts assistant/server.mjs on port 8020
npm run assistant:check    # checks guide.md against the interface
```

The assistant is a small dependency-free Node service. It sends `assistant/guide.md` and a short summary of the current page (scores and counts, never the paper or notes) to any OpenAI-compatible chat endpoint. It reads `ASSISTANT_API_KEY`, `ASSISTANT_BASE_URL` and `ASSISTANT_MODEL`, and falls back to `OPENAI_*` from `../backend/.env`. Without it, the chat window says the assistant is unavailable and nothing else changes. See [assistant/README.md](assistant/README.md) for how to maintain the guide.

## Commands

```bash
npm run dev              # dev server
npm test                 # Vitest suite
npm run lint             # oxlint
npm run build            # type check and production build
npm run preview          # preview the production build
npm run assistant        # help assistant service
npm run assistant:check  # static check of the assistant guide
```

## Code map

- `src/main.tsx`, `src/router.tsx`: entry point and routes (`/`, `/check/$id`, `/check/$id/print`, `/score/$id`, `/reports`).
- `src/app/`: the shell and title bar tabs, API selection (`VITE_API_MODE`), queries, pairing a credibility check with its quality assessment, run deletion, and PDF storage.
- `src/features/home/`: the start page.
- `src/features/check/`: the waiting page, live progress and the two-part run page.
- `src/features/report/`: the PDF reader, report document, widgets, outline and printable report.
- `src/features/score/`: the five-dimension quality report.
- `src/features/reports/`: history.
- `src/features/assistant/`: the help assistant chat window.
- `src/ui/`: shared controls (buttons, pills, progress dial, score gauge, model picker, widget frame, icons, motion).
- `src/api/`: HTTP adapters (`http*Api.ts`) and mocks (`mock*Api.ts`) for both assessment types.
- `src/types/`: types for field contract v0.4 and the credibility contract `credibility-0.5.0-draft`.
- `src/styles/`: design tokens (`theme.css`) and component styles. The rules behind them are in [docs/design/DESIGN_SYSTEM.zh-CN.md](docs/design/DESIGN_SYSTEM.zh-CN.md).
- `src/utils/arxiv.ts`: arXiv input normalization, using the same rules as the backend's `app/parsing/arxiv.py`.
- `public/brand/`: the PaperTrace logo and icon.
- `assistant/`: the help assistant service, its guide and checks.
- `src/components/`, `src/App.tsx`, `src/hooks/`: the earlier single-page interface, kept with its tests.

## Display rules

- **Unknown is not zero.** A dimension without a score shows why. A check that cannot finish gets a fixed fallback score of 3 and says so; it is never presented as a finding.
- **Every finding leads back to a source.** Highlights and evidence chips open the passage, reference or code source behind them.
- **Scores come from the backend.** Scores, categories, evidence relations and the model options are computed by the backend; the frontend never recomputes them.

## Deployment

The `/v1` and `/assistant` proxies only exist in `npm run dev`. A production build or `npm run preview` needs a reverse proxy for both paths. Model API keys belong to the backend and the assistant service only; never put them in the frontend or in a `VITE_` variable.
