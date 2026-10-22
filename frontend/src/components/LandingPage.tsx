import { rubricDimensions } from '../constants/labels'

interface Props {
  onStart: () => void
}

/** The question each rubric dimension is actually answering, per MVP baseline §6. */
const dimensionQuestions: Record<string, string> = {
  research_value: 'What problem does it solve, and would anyone use, cite or build on the result?',
  soundness_evidence: 'Do the paper’s own methods and experiments actually back its main claims?',
  originality_positioning: 'Is the contribution clearly separated from the work it cites?',
  clarity_usability: 'Can a reader follow the problem, the method and the conclusion?',
  reproducibility_transparency: 'Code, data, seeds, hyperparameters, hardware — what is genuinely provided?',
}

const flow = [
  { title: 'Submit', note: 'An arXiv ID or link, or a PDF from your machine.' },
  { title: 'Parse', note: 'Body text, sections, page numbers, tables and figures.' },
  { title: 'Score', note: 'Five dimensions, 1–10 each, against a versioned rubric.' },
  { title: 'Check', note: 'Every judgement links back to the passage it came from.' },
]

const boundaries = [
  ['It does not run the code', 'No experiment is reproduced and no repository is executed. Reproducibility is judged from what the paper reports.'],
  ['It does not predict accept or reject', 'There is no acceptance probability. A score is a reading decision, not a verdict on the paper.'],
  ['It does not prove novelty', 'No exhaustive prior-art search. It can only judge how clearly the paper positions itself against what it cites.'],
  ['It does not replace peer review', 'An unofficial, ICLR-inspired rubric for English machine learning papers. Treat it as a first pass.'],
]

/** Static illustration of the report — no data dependency, nothing is fetched. */
const previewDimensions = [
  { name: 'Research Value', score: 8 },
  { name: 'Soundness', score: 7 },
  { name: 'Originality', score: 8 },
  { name: 'Clarity', score: 8 },
  { name: 'Reproducibility', score: 7 },
]

export function LandingPage({ onStart }: Props) {
  return (
    <section className="view landing" aria-labelledby="landing-title">
      <div className="hero">
        <div className="hero-copy">
          <h1 id="landing-title">Read the paper that is worth reading</h1>
          <p className="hero-lead">
            Paper Credibility Check scores a machine learning paper across five dimensions and anchors every
            judgement to the page, section, table or figure it came from — so you can check
            the reasoning instead of trusting it.
          </p>
          <div className="cta-row">
            <button className="button button-primary" type="button" onClick={onStart}>
              Assess a paper
            </button>
            <a className="button button-secondary" href="#how-it-works">How it works</a>
          </div>
          <p className="hero-meta">
            ICLR-inspired rubric · English ML papers · unofficial, not a peer review
          </p>
        </div>

        <figure className="preview" aria-label="Example of the report this produces">
          <figcaption>Example report</figcaption>
          <div className="preview-top">
            <div className="preview-score">
              <b>7.6</b><span>/ 10</span>
            </div>
            <div>
              <strong>Worth reading, with checks</strong>
              <small>Medium confidence · 5 dimensions</small>
            </div>
          </div>
          <div className="preview-ticks" aria-hidden="true">
            {Array.from({ length: 10 }, (_, index) => (
              <i className={index < 8 ? 'is-on' : ''} key={index} />
            ))}
          </div>
          <div className="preview-dims">
            {previewDimensions.map((item) => (
              <div className="preview-dim" key={item.name}>
                <span>{item.name}</span>
                <i><em style={{ width: `${item.score * 10}%` }} /></i>
                <b>{item.score}</b>
              </div>
            ))}
          </div>
          <p className="preview-note">
            <span aria-hidden="true">●</span> Every claim carries its own evidence link
          </p>
        </figure>
      </div>

      <section className="landing-section" aria-labelledby="scored-title">
        <h2 id="scored-title">What gets scored</h2>
        <p className="section-lead">
          Five fixed dimensions, equally weighted. A dimension with no reliable evidence stays
          unscored rather than defaulting to zero.
        </p>
        <div className="dim-cards">
          {rubricDimensions.map((item) => (
            <article className="dim-card" key={item.id}>
              <h3>{item.name}</h3>
              <p>{dimensionQuestions[item.id]}</p>
              <span className="chip">{Math.round(item.weight * 100)}% weight</span>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section" id="how-it-works" aria-labelledby="how-title">
        <h2 id="how-title">How it works</h2>
        <ol className="flow">
          {flow.map((step, index) => (
            <li className="flow-step" key={step.title}>
              <span className="flow-index" aria-hidden="true">{index + 1}</span>
              <strong>{step.title}</strong>
              <p>{step.note}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="landing-section" aria-labelledby="limits-title">
        <h2 id="limits-title">What it will not do</h2>
        <p className="section-lead">
          Worth saying out loud, because a score invites more trust than it has earned.
        </p>
        <div className="boundaries">
          {boundaries.map(([title, note]) => (
            <div className="boundary" key={title}>
              <strong>{title}</strong>
              <p>{note}</p>
            </div>
          ))}
        </div>
      </section>

      <div className="landing-cta">
        <div>
          <h2>Ready to try it?</h2>
          <p>Paste an arXiv ID, or upload a PDF.</p>
        </div>
        <button className="button button-primary" type="button" onClick={onStart}>
          Assess a paper
        </button>
      </div>
    </section>
  )
}
