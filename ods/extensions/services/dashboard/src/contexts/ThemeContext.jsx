import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react'
import { WALLPAPERS } from '../lib/wallpapers'
import { readCustomWallpapers, addCustomWallpaper, deleteCustomWallpaper, isCustomWallpaper, CUSTOM_WALLPAPER_EVENT } from '../lib/customWallpapers'

const STORAGE_KEY = 'ods-theme'
const THEMES = WALLPAPERS.map(item => item.id)
const THEME_LABELS = Object.fromEntries(WALLPAPERS.map(item => [item.id, item.name]))
const DEFAULT_THEME = 'ods'

const ThemeContext = createContext(null)

export function ThemeProvider({ children }) {
  const [custom, setCustom] = useState([])
  const [wallpaperError, setWallpaperError] = useState('')
  const [wallpaperMotion, setWallpaperMotionState] = useState(() => {
    try { return localStorage.getItem('ods-wallpaper-motion') !== 'paused' } catch { return true }
  })
  const galleryRevision = useRef(0)
  const wallpapers = [...WALLPAPERS, ...custom]
  const [theme, setThemeState] = useState(() => {
    let stored
    try { stored = localStorage.getItem(STORAGE_KEY) } catch { /* Use Pixel when storage is unavailable. */ }
    return THEMES.includes(stored) || isCustomWallpaper(stored) ? stored : DEFAULT_THEME
  })

  useEffect(() => {
    let active = true
    const refresh = () => {
      const revision = ++galleryRevision.current
      return readCustomWallpapers().then(rows => {
        if (!active || revision !== galleryRevision.current) return
        setCustom(previous => {
          // Stored records are immutable: imports allocate new IDs. IndexedDB
          // clones Blobs on reads; retain live resources for unchanged IDs.
          const existing = new Map(previous.map(row => [row.id,row]))
          return rows.map(row => existing.get(row.id) ?? row)
        })
        setWallpaperError('')
        setThemeState(previous => isCustomWallpaper(previous) && !rows.some(row => row.id === previous) ? DEFAULT_THEME : previous)
      }).catch(error => { if (active && revision === galleryRevision.current) setWallpaperError(error.message) })
    }
    const sync = event => {
      if (event.key === 'ods-wallpaper-motion') { setWallpaperMotionState(event.newValue !== 'paused'); return }
      if (event.key !== STORAGE_KEY) return
      setThemeState(THEMES.includes(event.newValue) || isCustomWallpaper(event.newValue) ? event.newValue : DEFAULT_THEME)
      void refresh()
    }
    void refresh()
    window.addEventListener(CUSTOM_WALLPAPER_EVENT, refresh)
    window.addEventListener('focus', refresh)
    window.addEventListener('storage', sync)
    return () => { active = false; window.removeEventListener(CUSTOM_WALLPAPER_EVENT, refresh); window.removeEventListener('focus', refresh); window.removeEventListener('storage', sync) }
  }, [])

  useEffect(() => {
    const root = document.documentElement
    root.setAttribute('data-theme', 'ods')
    const wallpaper = [...WALLPAPERS, ...custom].find(item => item.id === theme)
    if (wallpaper?.image) {
      root.setAttribute('data-wallpaper', theme)
      root.setAttribute('data-wallpaper-kind', wallpaper.kind === 'video' ? 'video' : 'image')
      root.style.setProperty('--workspace-wallpaper', `url("${wallpaper.image}")`)
    } else {
      root.removeAttribute('data-wallpaper')
      root.removeAttribute('data-wallpaper-kind')
      root.style.removeProperty('--workspace-wallpaper')
    }
    try { localStorage.setItem(STORAGE_KEY, theme) } catch { /* Session-only theme. */ }
  }, [theme, custom])

  const setWallpaperMotion = useCallback(enabled => {
    setWallpaperMotionState(Boolean(enabled))
    try { localStorage.setItem('ods-wallpaper-motion', enabled ? 'playing' : 'paused') } catch { /* Session-only preference. */ }
  }, [])

  const setTheme = useCallback((t) => {
    if (THEMES.includes(t) || isCustomWallpaper(t)) setThemeState(t)
  }, [])

  const addWallpaper = async file => {
    const row = await addCustomWallpaper(file)
    galleryRevision.current++
    setCustom(previous => [...previous.filter(item => item.id !== row.id), row])
    setThemeState(row.id)
  }
  const removeWallpaper = async id => {
    await deleteCustomWallpaper(id)
    galleryRevision.current++
    setCustom(previous => previous.filter(item => item.id !== id))
    setThemeState(previous => previous === id ? DEFAULT_THEME : previous)
  }

  const cycleTheme = useCallback(() => {
    setThemeState(prev => {
      const idx = THEMES.indexOf(prev)
      return THEMES[(idx + 1) % THEMES.length]
    })
  }, [])

  return (
    <ThemeContext.Provider value={{ theme, setTheme, cycleTheme, themes: wallpapers.map(item => item.id), labels: {...THEME_LABELS, ...Object.fromEntries(custom.map(item => [item.id, item.name]))}, wallpapers, addWallpaper, removeWallpaper, wallpaperError, wallpaperMotion, setWallpaperMotion }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}
