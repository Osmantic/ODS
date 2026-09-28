import {fireEvent,render,screen} from '@testing-library/react'
import {beforeEach,describe,expect,it} from 'vitest'
import LanguageSelector from './LanguageSelector'
import {LANGUAGE_KEY} from './index'

describe('LanguageSelector', () => {
  beforeEach(() => localStorage.clear())

  it('changes and persists the selected language', () => {
    render(<LanguageSelector />)
    const select = screen.getByRole('combobox', {name: 'Language'})

    fireEvent.change(select, {target: {value: 'es'}})

    expect(select).toHaveValue('es')
    expect(localStorage.getItem(LANGUAGE_KEY)).toBe('es')
    expect(screen.getByText('Idioma')).toBeInTheDocument()
  })
})
