import { useTranslation } from 'react-i18next'
import { LANGUAGES } from './index'

/** Native select keeps language selection accessible by keyboard and screen readers. */
export default function LanguageSelector() {
  const { t, i18n } = useTranslation()

  return (
    <label className="flex items-center gap-2 text-xs text-theme-text-muted">
      <span>{t('common.language')}</span>
      <select
        value={i18n.resolvedLanguage || i18n.language}
        onChange={(event) => { void i18n.changeLanguage(event.target.value) }}
        aria-label={t('common.language')}
        className="rounded-lg border border-theme-border bg-theme-card text-theme-text px-2 py-1.5"
      >
        {LANGUAGES.map(({ code, label }) => (
          <option key={code} value={code}>{label}</option>
        ))}
      </select>
    </label>
  )
}
