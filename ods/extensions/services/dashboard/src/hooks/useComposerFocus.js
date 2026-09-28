import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'

const hasSelection = () => Boolean(window.getSelection()?.toString())
// A persistent notice (for example the install banner) opts out with
// data-composer-focus-ignore so it cannot disable focus restoration for good.
const hasOverlay = () => Array.from(document.querySelectorAll(
  'dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"], [role="menu"], [role="listbox"]',
)).some(element => !element.closest('[hidden], [aria-hidden="true"], [data-composer-focus-ignore]'))
const isPageFocused = () => [document.body, document.documentElement].includes(document.activeElement)
const isHidden = field => !field.isConnected || Boolean(field.closest('[hidden], [aria-hidden="true"], [inert]'))

// Disabled textareas lose browser focus during a turn. Restore only the user's
// composer focus, never focus that has since moved to another control or window.
export function useComposerFocus({ inputRef, disabled, visible = true, onType }) {
  const pending = useRef(true)
  const initial = useRef(true)
  const pendingCaret = useRef(null)

  useEffect(() => {
    const focused = event => {
      pending.current = event.target === inputRef.current
      if (pending.current) initial.current = false
    }
    const pointer = event => {
      if (event.target !== inputRef.current) pending.current = false
    }
    const blur = () => { pending.current = false }
    const keydown = event => {
      if (event.key === 'Tab' || event.key === 'Escape') pending.current = false
      if (disabled || !visible || event.defaultPrevented || event.isComposing || event.keyCode === 229
        || event.ctrlKey || event.metaKey || event.altKey || Array.from(event.key).length !== 1
        || ![document.body, document.documentElement].includes(event.target)
        || !isPageFocused() || hasSelection() || hasOverlay()) return
      const field = inputRef.current
      if (!field || field.disabled || field.readOnly || isHidden(field)) return
      event.preventDefault()
      const start = field.selectionStart ?? field.value.length
      const end = field.selectionEnd ?? start
      pendingCaret.current = start + event.key.length
      // Do not rely on browsers retargeting the original key after focus changes.
      onType(event.key, start, end)
      field.focus({ preventScroll: true })
    }
    document.addEventListener('focusin', focused)
    document.addEventListener('pointerdown', pointer)
    document.addEventListener('keydown', keydown)
    window.addEventListener('blur', blur)
    return () => {
      document.removeEventListener('focusin', focused)
      document.removeEventListener('pointerdown', pointer)
      document.removeEventListener('keydown', keydown)
      window.removeEventListener('blur', blur)
    }
  }, [inputRef, disabled, visible, onType])

  // Apply the caret only after React commits the value produced by onType.
  useLayoutEffect(() => {
    const caret = pendingCaret.current
    if (caret === null) return
    pendingCaret.current = null
    const field = inputRef.current
    if (!field || document.activeElement !== field || field.disabled || field.readOnly || !visible || isHidden(field)) return
    const position = Math.min(caret, field.value.length)
    field.setSelectionRange(position, position)
  })

  useEffect(() => {
    if (disabled || !visible || !pending.current) return
    pending.current = false
    const field = inputRef.current
    // Opening the page on touch devices should not summon the software keyboard.
    const touchEntry = initial.current && window.matchMedia?.('(pointer: coarse)').matches
    initial.current = false
    if (!touchEntry && field && !field.disabled && !field.readOnly && !isHidden(field)
      && (isPageFocused() || document.activeElement === field) && !hasSelection() && !hasOverlay()) {
      field.focus({ preventScroll: true })
    }
  }, [disabled, visible, inputRef])

  return useCallback(() => {
    pending.current = true
    initial.current = false
    const field = inputRef.current
    if (visible && field && !field.disabled && !field.readOnly && !isHidden(field)) {
      field.focus({ preventScroll: true })
    }
  }, [inputRef, visible])
}
