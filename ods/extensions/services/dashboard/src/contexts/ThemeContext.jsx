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

// ThemeProvider wraps the whole app, so an unguarded storage access here takes
// the entire dashboard down to the error boundary rather than degrading to the
// default theme. Browsers throw on localStorage in more cases than "quota
// exceeded" — blocking site data raises SecurityError on the property access
// itself, before getItem is ever called. Same shape as App.jsx's
// getStorageValue / setStorageValue.
function readStoredTheme() {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

function writeStoredTheme(theme) {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, theme)
  } catch {
    // Private windows and restricted environments: the theme still applies for
    // this session, it just will not be remembered across reloads.
  }
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(() => {
    const stored = readStoredTheme()
    return THEMES.includes(stored) ? stored : DEFAULT_THEME
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    writeStoredTheme(theme)
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
