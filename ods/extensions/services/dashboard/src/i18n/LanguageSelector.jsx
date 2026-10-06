import {LANGUAGES,useI18n} from './index'

/**
 * Small dependency-free language selector shared by FirstBoot and Settings.
 *
 * Keeping this component native (a <select>) avoids adding another UI
 * dependency and keeps keyboard/accessibility behavior provided by the
 * browser. Language names are intentionally displayed in their native form.
 */
export default function LanguageSelector() {
  const {language,setLanguage,t} = useI18n()

  return <label className="firstboot-language">
    <span>{t('common.language')}</span>
    <select value={language} onChange={event => setLanguage(event.target.value)} aria-label={t('common.language')}>
      {LANGUAGES.map(item => <option key={item.code} value={item.code}>{item.label}</option>)}
    </select>
  </label>
}
