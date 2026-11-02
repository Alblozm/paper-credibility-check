import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Icon, type IconName } from './Icon'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'quiet'
  size?: 'md' | 'sm'
  icon?: IconName
  iconEnd?: IconName
  on?: boolean
  children?: ReactNode
}

export function Button({ variant = 'secondary', size = 'md', icon, iconEnd, on, className = '', children, type = 'button', ...rest }: ButtonProps) {
  const classes = ['cs-btn', variant === 'primary' && 'is-primary', variant === 'quiet' && 'is-quiet', size === 'sm' && 'is-sm', on && 'is-on', className]
  return <button type={type} className={classes.filter(Boolean).join(' ')} {...rest}>
    {icon && <Icon name={icon} />}{children}{iconEnd && <Icon name={iconEnd} />}
  </button>
}

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { icon: IconName; label: string; small?: boolean; pressed?: boolean }

export function IconButton({ icon, label, small, pressed, className = '', type = 'button', ...rest }: IconButtonProps) {
  return <button type={type} className={`cs-ibtn ${small ? 'is-sm' : ''} ${className}`} title={label} aria-label={label}
    aria-pressed={pressed} {...rest}><Icon name={icon} /></button>
}
