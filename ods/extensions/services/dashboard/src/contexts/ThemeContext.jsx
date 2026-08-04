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

function readStoredTheme() {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY)
  } catch (error) {
    if (error instanceof globalThis.DOMException) return null
    throw error
  }
}

function persistTheme(theme) {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, theme)
  } catch (error) {
    if (!(error instanceof globalThis.DOMException)) throw error
  }
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(() => {
    const stored = readStoredTheme()
    return THEMES.includes(stored) ? stored : DEFAULT_THEME
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    persistTheme(theme)
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
