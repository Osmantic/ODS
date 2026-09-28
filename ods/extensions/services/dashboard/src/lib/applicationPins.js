// Installed extensions that have a web page appear under Applications in the
// sidebar. They are pinned by default and a fresh install pins again; the
// owner can unpin one from its guide. Like the local profile this is a
// per-browser preference: when storage is unavailable (private window,
// blocked site data) unpinning lasts until the page reloads.

const STORAGE_NAME = 'ods.applications.unpinned.v1'
const ID = /^[a-z0-9][a-z0-9_-]{0,63}$/
export const APPLICATIONS_CHANGED = 'ods:applications-changed'

let memory = []

function clean(ids) {
  return Array.isArray(ids) ? [...new Set(ids.filter(id => typeof id === 'string' && ID.test(id)))].slice(0, 512) : []
}

export function readUnpinned() {
  try {
    const stored = localStorage.getItem(STORAGE_NAME)
    if (stored !== null) return clean(JSON.parse(stored))
  } catch { /* unreadable storage: fall back to this page's memory */ }
  return clean(memory)
}

export function isPinned(id) {
  return !readUnpinned().includes(id)
}

// Tell the sidebar to reload Applications (an install, removal or pin change).
export function notifyApplicationsChanged() {
  window.dispatchEvent(new CustomEvent(APPLICATIONS_CHANGED))
}

export function setPinned(id, pinned) {
  if (!ID.test(id || '')) return
  const current = readUnpinned().filter(other => other !== id)
  const next = pinned ? current : [...current, id]
  memory = next
  try { localStorage.setItem(STORAGE_NAME, JSON.stringify(next)) } catch { /* kept in memory for this page */ }
  notifyApplicationsChanged()
}
