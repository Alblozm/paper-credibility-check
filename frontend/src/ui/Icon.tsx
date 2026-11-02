import { icons, type IconName } from './icons'

/** Fluent System Icon. Decorative: give the surrounding control its accessible name. */
export function Icon({ name, className = '' }: { name: IconName; className?: string }) {
  return <svg className={`cs-fi ${className}`} viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"
    dangerouslySetInnerHTML={{ __html: icons[name] }} />
}

export type { IconName }
