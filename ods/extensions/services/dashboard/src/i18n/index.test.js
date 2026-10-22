import { beforeEach, describe, expect, it } from 'vitest'
import i18n, { LANGUAGE_KEY, persistLanguage, readSavedLanguage } from './index'

describe('i18next configuration', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en')
    // changeLanguage persists the locale; clear after it to isolate each test.
    localStorage.clear()
  })

  it('uses English when the saved locale is missing or unsupported', () => {
    expect(readSavedLanguage()).toBe('en')
    localStorage.setItem(LANGUAGE_KEY, 'fr')
    expect(readSavedLanguage()).toBe('en')
  })

  it('uses translations and leaves unknown keys readable', async () => {
    await i18n.changeLanguage('es')
    expect(i18n.t('profile.save')).toBe('Guardar perfil')
    expect(i18n.t('a.key.not.in.catalog')).toBe('a.key.not.in.catalog')
  })

  it('interpolates values in translated strings', () => {
    expect(i18n.t('firstBoot.done.body', { username: 'Alice' })).toContain('Alice')
  })

  it('ignores unsupported languages when persisting', () => {
    expect(() => persistLanguage('fr')).not.toThrow()
    expect(localStorage.getItem(LANGUAGE_KEY)).toBeNull()
  })
})
