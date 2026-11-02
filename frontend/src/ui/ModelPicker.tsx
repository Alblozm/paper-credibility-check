import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { Icon } from './Icon'
import { ProviderMark } from './ProviderMark'

export interface PickerModel { id: string; name: string; provider: string; description: string; available: boolean }

interface Props {
  models: PickerModel[]
  value: string | null
  onChange: (id: string) => void
  disabled?: boolean
}

/**
 * Model picker (spec 3.10): a listbox that shows each model's provider mark. Models the backend lists but cannot run yet
 * stay visible and disabled, so the catalogue reads the same before and after they are switched on.
 */
export function ModelPicker({ models, value, onChange, disabled = false }: Props) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(value)
  const root = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLUListElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const id = useId()
  const current = models.find(model => model.id === value)
  const usable = models.filter(model => model.available)

  useEffect(() => {
    if (!open) return
    list.current?.focus()
    const away = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [open])

  const show = () => { setActive(value); setOpen(true) }
  const close = () => { setOpen(false); button.current?.focus() }
  const choose = (model: PickerModel | undefined) => {
    if (!model?.available) return
    onChange(model.id)
    close()
  }
  const move = (to: number | 'first' | 'last') => {
    const at = usable.findIndex(model => model.id === active)
    const next = to === 'first' ? usable[0] : to === 'last' ? usable.at(-1) : usable[(at + to + usable.length) % usable.length]
    if (next) setActive(next.id)
  }
  const onListKey = (event: KeyboardEvent) => {
    switch (event.key) {
      case 'ArrowDown': move(1); break
      case 'ArrowUp': move(-1); break
      case 'Home': move('first'); break
      case 'End': move('last'); break
      case 'Enter': case ' ': choose(models.find(model => model.id === active)); break
      case 'Escape': close(); break
      case 'Tab': setOpen(false); return
      default: return
    }
    event.preventDefault()
  }

  return <div ref={root} className={`cs-picker ${open ? 'is-open' : ''}`}>
    <span id={`${id}-label`}>Model</span>
    <button ref={button} type="button" className="cs-picker-btn" disabled={disabled} aria-haspopup="listbox" aria-expanded={open}
      aria-labelledby={`${id}-label ${id}-value`} onClick={() => (open ? close() : show())}
      onKeyDown={event => { if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); show() } }}>
      {current && <ProviderMark provider={current.provider} />}
      <span id={`${id}-value`}>{current?.name ?? 'Select a model'}</span>
      <Icon name="chevron_down" className="is-chev" />
    </button>
    {open && <ul ref={list} role="listbox" tabIndex={-1} aria-labelledby={`${id}-label`} aria-activedescendant={active ? `${id}-${active}` : undefined}
      className="cs-picker-list cs-glass" onKeyDown={onListKey}>
      {models.map(model => <li key={model.id} id={`${id}-${model.id}`} role="option" aria-selected={model.id === value} aria-disabled={!model.available}
        className={model.id === active ? 'is-active' : ''} onPointerEnter={() => { if (model.available) setActive(model.id) }} onClick={() => choose(model)}>
        <ProviderMark provider={model.provider} />
        <span className="is-text"><b>{model.name}</b>{model.description && <small>{model.description}</small>}</span>
        {model.id === value ? <Icon name="checkmark" className="is-tick" /> : !model.available && <span className="is-tag">Unavailable</span>}
      </li>)}
    </ul>}
  </div>
}
