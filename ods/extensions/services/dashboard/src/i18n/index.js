import {useSyncExternalStore} from 'react'
import en from './en'
import es from './es'
import zhCN from './zh-CN'

export const LANGUAGE_KEY = 'ods.language.v1'
export const LANGUAGES = [
  {code: 'en', label: 'English'},
  {code: 'es', label: 'Español'},
  {code: 'zh-CN', label: '简体中文'},
]

const dictionaries = {en, es, 'zh-CN': zhCN}
const EVENT = 'ods:language-changed'

/**
 * Keep language validation in one place so persisted values and UI changes
 * follow exactly the same supported-language rules.
 */
function normalizeLanguage(value) {
  return LANGUAGES.some(({code}) => code === value) ? value : 'en'
}

/**
 * Read the persisted dashboard language.
 *
 * localStorage is intentionally treated as optional: private browsing,
 * browser policy, or storage quotas must never prevent the dashboard from
 * rendering. Invalid or missing values therefore resolve to English.
 */
export function readLanguage() {
  try {
    return normalizeLanguage(window.localStorage.getItem(LANGUAGE_KEY))
  } catch {
    return 'en'
  }
}

/**
 * Persist a supported language and notify components in this browser.
 *
 * The custom event is needed because the browser "storage" event does not
 * fire in the same document that performed the localStorage write.
 */
export function saveLanguage(value) {
  const language = normalizeLanguage(value)
  try {
    window.localStorage.setItem(LANGUAGE_KEY, language)
  } catch {
    // Storage failures must not prevent the dashboard from changing language.
  }
  window.dispatchEvent(new Event(EVENT))
  return language
}

function subscribe(callback) {
  window.addEventListener(EVENT, callback)
  window.addEventListener('storage', callback)
  return () => {
    window.removeEventListener(EVENT, callback)
    window.removeEventListener('storage', callback)
  }
}

/**
 * Apply simple named placeholders such as {username} without introducing a
 * template dependency. Unknown placeholders remain visible instead of being
 * silently discarded, which makes incomplete translations easier to detect.
 */
function interpolate(value, vars = {}) {
  return value.replace(/\{(\w+)\}/g, (_, key) =>
    Object.prototype.hasOwnProperty.call(vars, key) ? String(vars[key]) : '{' + key + '}'
  )
}

/**
 * Resolve a translation using the selected dictionary, then English, then
 * the key itself. This guarantees a deterministic fallback for partial
 * translations and newly introduced UI strings.
 */
export function translate(language, key, vars) {
  const value = dictionaries[normalizeLanguage(language)]?.[key] ?? en[key] ?? key
  return interpolate(value, vars)
}

/**
 * React hook used by dashboard components. useSyncExternalStore keeps the
 * selector and translated UI synchronized without adding a context provider
 * or changing the dashboard application's top-level composition.
 */
export function useI18n() {
  const language = useSyncExternalStore(subscribe, readLanguage, () => 'en')
  return {language, setLanguage: saveLanguage, t: (key, vars) => translate(language, key, vars)}
}
