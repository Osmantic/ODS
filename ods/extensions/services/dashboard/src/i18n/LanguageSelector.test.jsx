import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import LanguageSelector from './LanguageSelector'
import i18n, { LANGUAGE_KEY } from './index'

describe('LanguageSelector', () => {
  beforeEach(async () => {
    localStorage.clear()
    await i18n.changeLanguage('en')
  })

  it('changes language and persists the selection', async () => {
    render(<LanguageSelector />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Language' }), { target: { value: 'es' } })

    await waitFor(() => expect(screen.getByText('Idioma')).toBeInTheDocument())
    expect(localStorage.getItem(LANGUAGE_KEY)).toBe('es')
    expect(screen.getByRole('combobox', { name: 'Idioma' })).toHaveValue('es')
  })
})
