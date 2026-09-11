import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { WALLPAPERS } from '../lib/wallpapers'

const STORAGE_KEY = 'ods-theme'
const THEMES = WALLPAPERS.map(item => item.id)
const THEME_LABELS = Object.fromEntries(WALLPAPERS.map(item => [item.id, item.name]))
const DEFAULT_THEME = 'ods'

const ThemeContext = createContext(null)

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(() => {
    let stored
    try { stored = localStorage.getItem(STORAGE_KEY) } catch { /* Use Pixel when storage is unavailable. */ }
    return THEMES.includes(stored) ? stored : DEFAULT_THEME
  })

  useEffect(() => {
    const root = document.documentElement
    root.setAttribute('data-theme', 'ods')
    const wallpaper = WALLPAPERS.find(item => item.id === theme)
    if (wallpaper?.image) {
      root.setAttribute('data-wallpaper', theme)
      root.style.setProperty('--workspace-wallpaper', `url("${wallpaper.image}")`)
    } else {
      root.removeAttribute('data-wallpaper')
      root.style.removeProperty('--workspace-wallpaper')
    }
    try { localStorage.setItem(STORAGE_KEY, theme) } catch { /* Session-only theme. */ }
  }, [theme])

  useEffect(() => {
    const sync = event => { if (event.key === STORAGE_KEY) setThemeState(THEMES.includes(event.newValue) ? event.newValue : DEFAULT_THEME) }
    window.addEventListener('storage', sync)
    return () => window.removeEventListener('storage', sync)
  }, [])

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
