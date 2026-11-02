import type { HTMLAttributes, ReactNode } from 'react'
import { IconButton } from './Button'
import { Icon, type IconName } from './Icon'
import { Badge } from './Pill'

interface Props extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  id: string
  icon: IconName
  title: string
  badge?: number | null
  badgeGap?: boolean
  /** Controls placed before the collapse button, e.g. a style switch. */
  extra?: ReactNode
  collapsed?: boolean
  onToggle?: () => void
  onClose?: () => void
  /** Called when the grip is pressed, so the dock can make the card draggable. */
  onGrip?: () => void
  children: ReactNode
}

/** Dock card with a header row: grip, icon, title, count, extras, close and collapse (spec 3.6). */
export function Widget({ id, icon, title, badge, badgeGap, extra, collapsed = false, onToggle, onClose, onGrip, children, className = '', ...rest }: Props) {
  return <section className={`cs-widget ${collapsed ? 'is-collapsed' : ''} ${className}`} data-widget={id} aria-label={title} {...rest}>
    <header>
      <span className="is-grip" title="Drag to reorder" onPointerDown={onGrip} aria-hidden="true"><Icon name="re_order_dots_vertical" /></span>
      <span className="is-icon"><Icon name={icon} /></span>
      <h3>{title}</h3>
      {badge != null && <Badge gap={badgeGap}>{badge}</Badge>}
      <span className="flex-1" />
      {extra}
      {onClose && <IconButton icon="dismiss" label={`Remove ${title}`} small onClick={onClose} />}
      {onToggle && <IconButton icon="chevron_down" label={collapsed ? `Expand ${title}` : `Collapse ${title}`} small className="is-chev" onClick={onToggle} aria-expanded={!collapsed} />}
    </header>
    <div className="cs-widget-body">{children}</div>
  </section>
}
