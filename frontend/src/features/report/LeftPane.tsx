import { useEffect, useRef, useState } from 'react'
import type { PDFPageProxy } from 'pdfjs-dist'
import { Icon, type IconName } from '../../ui/Icon'
import { Toggle } from '../../ui/Controls'
import { markCount, skills, type ReportModel, type SkillId } from './model'

type Tab = 'outline' | 'pages' | 'skills'
const tabs: { id: Tab; label: string; icon: IconName }[] = [
  { id: 'outline', label: 'Outline', icon: 'text_bullet_list_ltr' },
  { id: 'pages', label: 'Pages', icon: 'grid' },
  { id: 'skills', label: 'Skills', icon: 'puzzle_piece' },
]

function Thumb({ page, n, on, claims, refs, onOpen }: { page: PDFPageProxy; n: number; on: boolean; claims: number; refs: number; onOpen: () => void }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const node = canvas.current
    if (!node) return
    let task: ReturnType<PDFPageProxy['render']> | null = null
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return
      observer.disconnect()
      const viewport = page.getViewport({ scale: 220 / page.getViewport({ scale: 1 }).width })
      node.width = viewport.width; node.height = viewport.height
      task = page.render({ canvas: node, viewport })
      task.promise.catch(() => {})
    })
    observer.observe(node)
    return () => { observer.disconnect(); task?.cancel() }
  }, [page])
  return <button type="button" className={`cs-thumb ${on ? 'is-on' : ''}`} onClick={onOpen} aria-label={`Page ${n}`}>
    <span><canvas ref={canvas} /><span className="is-dots">{claims > 0 && <i className="cs-dot" />}{refs > 0 && <i className="cs-dot is-gap" />}</span></span>
    <span>{n}{claims ? ` · ${claims} claim` : ''}{refs ? ` · ${refs} ref` : ''}</span>
  </button>
}

interface Props {
  model: ReportModel
  pages: PDFPageProxy[]
  current: number
  layers: Record<SkillId, boolean>
  onLayer: (id: SkillId, on: boolean) => void
  onPage: (page: number) => void
}

export function LeftPane({ model, pages, current, layers, onLayer, onPage }: Props) {
  const [tab, setTab] = useState<Tab>('outline')
  const onSection = model.sections.reduce((found, section, i) => (section.page <= current ? i : found), -1)
  const countOn = (page: number, skill: SkillId) => [...model.marks.values()].filter(mark => mark.page === page && mark.skill === skill).length
  return <aside className="cs-pane" aria-label="Paper navigation">
    <nav className="cs-ptabs" role="tablist">
      {tabs.map(item => <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} onClick={() => setTab(item.id)}>
        <Icon name={item.icon} />{item.label}
      </button>)}
    </nav>
    <div className="px-2 pt-2.5 pb-8">
      {tab === 'outline' && <>
        <p className="cs-eyebrow mx-1.5 mt-1 mb-1.5">Sections with evidence</p>
        <ul className="cs-outline">{model.sections.map((section, i) => <li key={section.title}>
          <button type="button" className={i === onSection ? 'is-on' : ''} onClick={() => onPage(section.page)}>
            <span>{section.title}</span>
            <span className="is-meta">{section.claims > 0 && <><i className="cs-dot" />{section.claims}</>}{section.refs > 0 && <><i className="cs-dot is-gap" />{section.refs}</>}</span>
            <span className="is-meta">p. {section.page}</span>
          </button>
        </li>)}</ul>
        {!model.sections.length && <p className="cs-lead px-1.5">No passages were located in named sections.</p>}
      </>}
      {tab === 'pages' && (pages.length
        ? <div className="cs-thumbs">{pages.map((page, i) => <Thumb key={i} page={page} n={i + 1} on={current === i + 1}
            claims={countOn(i + 1, 'claims')} refs={countOn(i + 1, 'refs')} onOpen={() => onPage(i + 1)} />)}</div>
        : <p className="cs-lead px-1.5">Page previews appear once the PDF loads.</p>)}
      {tab === 'skills' && <>
        {skills.map(skill => {
          const dimension = model.dimensions[skill.dimension]
          return <section key={skill.id} className="cs-skill">
            <header><Icon name={skill.icon} /><h4>{skill.name}</h4><b>{dimension.score?.toFixed(1) ?? '—'}</b>
              <Toggle checked={layers[skill.id]} onChange={next => onLayer(skill.id, next)} label={`Show ${skill.name} highlights`} /></header>
            <p>{dimension.check_status === 'completed' ? 'Complete' : dimension.check_status}{model.fallback.has(skill.dimension) ? ' · fallback score' : ''} · {markCount(model, skill.id)} passages</p>
            {skill.legend.length ? <div className="cs-legend">{skill.legend.map(([kind, text]) => <span key={kind}><i className={`cs-sw ${kind}`} />{text}</span>)}</div>
              : <p>This check records no passages; it examines code links in the body and appendix.</p>}
          </section>
        })}
        <section className="cs-addskill">
          <h4><Icon name="add" />Additional checks (planned)</h4>
          <p>Each check contributes a score, a widget and a highlight layer.</p>
          <ul>
            <li><b>Statistical reporting</b>Checks reported p-values, intervals and sample sizes against the tables.</li>
            <li><b>Figure–text consistency</b>Compares numbers quoted in the text with the figures.</li>
            <li><b>Dataset licences</b>Looks up the licence of each dataset the paper uses.</li>
          </ul>
          <pre>{`// src/features/report/model.ts
skills.push({
  id: 'stats',
  name: 'Statistical reporting',
  dimension: 'statistical_reporting',
  icon: 'data_bar_horizontal',
  legend: [...],
})`}</pre>
        </section>
      </>}
    </div>
  </aside>
}
