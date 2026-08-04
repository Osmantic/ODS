export async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch (error) {
      console.debug('Clipboard API failed; trying the DOM fallback', error)
    }
  }

  if (!document.body || typeof document.execCommand !== 'function') return false

  const activeElement = document.activeElement
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.readOnly = true
  textarea.setAttribute('aria-hidden', 'true')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  textarea.style.pointerEvents = 'none'
  document.body.appendChild(textarea)
  textarea.select()
  textarea.setSelectionRange(0, textarea.value.length)

  try {
    return document.execCommand('copy')
  } catch (error) {
    console.debug('DOM clipboard fallback failed', error)
    return false
  } finally {
    textarea.remove()
    activeElement?.focus()
  }
}
