import {beforeEach,describe,expect,it} from 'vitest'
import {LANGUAGE_KEY,LANGUAGES,readLanguage,saveLanguage,translate} from './index'

describe('dashboard i18n', () => {
  beforeEach(() => localStorage.clear())

  it('defaults to English', () => {
    expect(readLanguage()).toBe('en')
    expect(translate('en', 'common.continue')).toBe('Continue')
  })

  it('persists supported languages', () => {
    saveLanguage('es')
    expect(readLanguage()).toBe('es')
    expect(saveLanguage('zh-CN')).toBe('zh-CN')
    expect(readLanguage()).toBe('zh-CN')
  })

  it('falls back to English for an invalid language', () => {
    localStorage.setItem(LANGUAGE_KEY, 'xx')
    expect(readLanguage()).toBe('en')
  })

  it('uses the requested dictionary', () => {
    expect(translate('es', 'profile.save')).toBe('Guardar perfil')
    expect(translate('zh-CN', 'profile.save')).toBe('保存个人资料')
  })

  it('falls back to the key when a translation is missing', () => {
    expect(translate('es', 'missing.key')).toBe('missing.key')
  })

  it('exposes the three supported languages', () => {
    expect(LANGUAGES.map(item => item.code)).toEqual(['en', 'es', 'zh-CN'])
  })
})
