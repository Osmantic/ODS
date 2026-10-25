import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './en'
import es from './es'
import zhCN from './zh-CN'

export const LANGUAGE_KEY = 'ods.language.v1'
export const LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'es', label: 'Español' },
  { code: 'zh-CN', label: '简体中文' },
]

const supportedLanguages = new Set(LANGUAGES.map(({ code }) => code))

export function readSavedLanguage() {
  try {
    const value = globalThis.localStorage?.getItem(LANGUAGE_KEY)
    return supportedLanguages.has(value) ? value : 'en'
  } catch {
    return 'en'
  }
}

export function persistLanguage(language) {
  if (!supportedLanguages.has(language)) return
  try {
    globalThis.localStorage?.setItem(LANGUAGE_KEY, language)
  } catch {
    // Storage is optional; language switching must still work for this session.
  }
}

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    es: { translation: es },
    'zh-CN': { translation: zhCN },
  },
  lng: readSavedLanguage(),
  fallbackLng: 'en',
  supportedLngs: [...supportedLanguages],
  load: 'currentOnly',
  keySeparator: false,
  interpolation: { escapeValue: false },
  returnNull: false,
  initImmediate: false,
})

i18n.on('languageChanged', persistLanguage)

export default i18n
