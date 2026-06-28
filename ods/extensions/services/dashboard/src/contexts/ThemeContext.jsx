import { createContext, useContext, useState, useEffect, useCallback } from 'react'

const STORAGE_KEY = 'ods-theme'
const THEMES = ['ods', 'lemonade', 'light', 'arctic']
const THEME_LABELS = {
  ods: 'ODS',
  lemonade: 'Lemonade',
  light: 'Light',
  arctic: 'Arctic'
}
const DEFAULT_THEME = 'ods'

const ThemeContext = createContext(null)

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    return THEMES.includes(stored) ? stored : DEFAULT_THEME
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  const setTheme = useCallback((t) => {
    if (THEMES.includes(t)) setThemeState(t)
  }, [])

  const cycleTheme = useCallback(() => {
    setThemeState(prev => {
      const idx = THEMES.indexOf(prev)
      return THEMES[(idx + 1) % THEMES.length]
    })
  }, [])

  return (
    <ThemeContext.Provider value={{ theme, setTheme, cycleTheme, themes: THEMES, labels: THEME_LABELS }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}
