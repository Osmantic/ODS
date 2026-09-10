import { useEffect, useId, useRef, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import './panel-select.css'

// Inline (not portalled) so long labels can never expand into the chat pane.
export default function PanelSelect({ label, value, onChange, options, disabled = false }) {
  const id = useId(), root = useRef(null), trigger = useRef(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const selected = options.findIndex(option => option.value === value)
  const choose = index => {
    if (!options[index]) return
    onChange(options[index].value)
    setOpen(false)
    trigger.current?.focus()
  }
  useEffect(() => {
    if (!open) return
    const outside = event => { if (!root.current?.contains(event.target)) setOpen(false) }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [open])
  useEffect(() => {
    if (open) root.current?.querySelector(`[id="${id}-option-${active}"]`)?.scrollIntoView?.({ block: 'nearest' })
  }, [open, active, id])
  const keyDown = event => {
    if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); setOpen(false); return }
    if (event.key === 'Tab') { setOpen(false); return }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault()
      setOpen(true)
      setActive(previous => {
        if (event.key === 'Home') return 0
        if (event.key === 'End') return options.length - 1
        if (!open) return Math.max(0, selected)
        return Math.max(0, Math.min(options.length - 1, previous + (event.key === 'ArrowDown' ? 1 : -1)))
      })
    } else if ((event.key === 'Enter' || event.key === ' ') && open) {
      event.preventDefault(); choose(active)
    }
  }
  return <div className="panel-select" ref={root} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false) }}>
    <button ref={trigger} type="button" role="combobox" aria-label={label} aria-expanded={open && !disabled}
      aria-controls={`${id}-list`} aria-haspopup="listbox" aria-activedescendant={open ? `${id}-option-${active}` : undefined}
      disabled={disabled || !options.length} onKeyDown={keyDown}
      onClick={() => { setActive(Math.max(0, selected)); setOpen(!open) }}>
      <span>{options[selected]?.label || 'No matching categories'}</span><ChevronDown size={14} />
    </button>
    {open && !disabled && <div id={`${id}-list`} role="listbox" aria-label={label} className="panel-select-options">
      {options.map((option, index) => <div key={option.value}>
        {option.group && option.group !== options[index - 1]?.group && <div className="panel-select-group">{option.group}</div>}
        <div id={`${id}-option-${index}`} role="option" aria-selected={option.value === value}
          className={active === index ? 'is-highlighted' : ''} onPointerDown={event => event.preventDefault()}
          onMouseEnter={() => setActive(index)} onClick={() => choose(index)}>
          <span>{option.label}</span>{option.value === value && <Check size={13} />}
        </div>
      </div>)}
    </div>}
  </div>
}
