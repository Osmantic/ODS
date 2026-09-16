import {render, screen, fireEvent, act} from '@testing-library/react'
import {ThemeProvider, useTheme} from './ThemeContext'
import {WALLPAPERS} from '../lib/wallpapers'

function Picker() { const {theme,setTheme,themes} = useTheme(); return <><output>{theme}</output>{themes.map(id=><button key={id} onClick={()=>setTheme(id)}>{id}</button>)}</> }
beforeEach(()=>localStorage.clear())
test('keeps Pixel as default and applies each local wallpaper without changing the base palette',()=>{
  render(<ThemeProvider><Picker/></ThemeProvider>)
  expect(document.documentElement).not.toHaveAttribute('data-wallpaper')
  for (const item of WALLPAPERS.filter(item=>item.image)) {
    fireEvent.click(screen.getByRole('button',{name:item.id,exact:true}))
    expect(document.documentElement).toHaveAttribute('data-theme','ods')
    expect(document.documentElement).toHaveAttribute('data-wallpaper',item.id)
    expect(document.documentElement.style.getPropertyValue('--workspace-wallpaper')).toContain(item.image)
    expect(localStorage.getItem('ods-theme')).toBe(item.id)
  }
  fireEvent.click(screen.getByRole('button',{name:'ods',exact:true}))
  expect(document.documentElement).not.toHaveAttribute('data-wallpaper')
  expect(document.documentElement.style.getPropertyValue('--workspace-wallpaper')).toBe('')
})
test('restores a saved wallpaper and synchronizes other tabs safely',()=>{
  localStorage.setItem('ods-theme','forest')
  render(<ThemeProvider><Picker/></ThemeProvider>)
  expect(document.documentElement).toHaveAttribute('data-wallpaper','forest')
  localStorage.setItem('ods-theme','nightfall')
  act(()=>window.dispatchEvent(new StorageEvent('storage',{key:'ods-theme',newValue:'nightfall'})))
  expect(document.documentElement).toHaveAttribute('data-wallpaper','nightfall')
  localStorage.setItem('ods-theme','unknown')
  act(()=>window.dispatchEvent(new StorageEvent('storage',{key:'ods-theme',newValue:'unknown'})))
  expect(document.documentElement).not.toHaveAttribute('data-wallpaper')
})

test('reads the latest stored choice instead of replaying an obsolete storage-event payload', () => {
  localStorage.setItem('ods-theme','forest')
  render(<ThemeProvider><Picker/></ThemeProvider>)
  localStorage.setItem('ods-theme','nightfall')
  act(() => window.dispatchEvent(new StorageEvent('storage',{key:'ods-theme',oldValue:'forest',newValue:'ods'})))
  expect(document.documentElement).toHaveAttribute('data-wallpaper','nightfall')
  expect(localStorage.getItem('ods-theme')).toBe('nightfall')
})

test('honors cross-tab storage clearing for both wallpaper and motion preferences', () => {
  function Motion() {return <output aria-label="Motion">{String(useTheme().wallpaperMotion)}</output>}
  localStorage.setItem('ods-theme','forest')
  localStorage.setItem('ods-wallpaper-motion','paused')
  render(<ThemeProvider><Picker/><Motion/></ThemeProvider>)
  expect(screen.getByLabelText('Motion')).toHaveTextContent('false')
  localStorage.clear()
  act(() => window.dispatchEvent(new StorageEvent('storage',{key:null})))
  expect(document.documentElement).not.toHaveAttribute('data-wallpaper')
  expect(screen.getByLabelText('Motion')).toHaveTextContent('true')
})

test('does not apply an older queued motion event over the stored pause preference', () => {
  function Motion() {return <output aria-label="Motion">{String(useTheme().wallpaperMotion)}</output>}
  render(<ThemeProvider><Motion/></ThemeProvider>)
  localStorage.setItem('ods-wallpaper-motion','paused')
  act(() => window.dispatchEvent(new StorageEvent('storage',{key:'ods-wallpaper-motion',newValue:'playing'})))
  expect(screen.getByLabelText('Motion')).toHaveTextContent('false')
})
