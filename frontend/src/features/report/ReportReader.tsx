import { useEffect, useRef } from 'react'
import { useSectionSpy } from '../../ui/useSectionSpy'
import { ReportDocument } from './documents'
import { documentOutline } from './outline'
import type { Reported } from './widgets'

/** Report view (spec 5.3): the credibility assessment as one document. The quality assessment has its own tab. */
export function ReportReader({ assessment, modelName }: { assessment: Reported; modelName: string }) {
  const outline = documentOutline(undefined, false)
  const scroller = useRef<HTMLDivElement>(null)
  const { on, go } = useSectionSpy(outline.map(section => section.id), scroller)

  // A link such as ?view=report#doc-quality opens at that section.
  useEffect(() => {
    const target = window.location.hash.slice(1)
    if (target) window.setTimeout(() => document.getElementById(target)?.scrollIntoView({ block: 'start' }), 50)
  }, [])

  return <div className="cs-docview">
    <nav className="cs-docview-toc" aria-label="Report sections">
      <div className="grid gap-1">
        <p className="cs-eyebrow mx-1.5">Credibility assessment</p>
        <ul className="cs-outline">{outline.map(section => <li key={section.id}>
          <button type="button" className={on === section.id ? 'is-on' : ''} aria-current={on === section.id ? 'true' : undefined}
            onClick={() => go(section.id)}><span>{section.label}</span></button>
        </li>)}</ul>
      </div>
    </nav>
    <div ref={scroller} className="cs-docview-scroll">
      <article className="cs-print is-screen">
        <ReportDocument assessment={assessment} modelName={modelName} />
      </article>
    </div>
  </div>
}
