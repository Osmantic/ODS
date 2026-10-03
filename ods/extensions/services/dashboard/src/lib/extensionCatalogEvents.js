export const EXTENSION_CATALOG_CHANGED = 'ods:extension-catalog-changed'

export function notifyExtensionCatalogChanged() {
  window.dispatchEvent(new Event(EXTENSION_CATALOG_CHANGED))
}
