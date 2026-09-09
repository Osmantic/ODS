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
  act(()=>window.dispatchEvent(new StorageEvent('storage',{key:'ods-theme',newValue:'nightfall'})))
  expect(document.documentElement).toHaveAttribute('data-wallpaper','nightfall')
  act(()=>window.dispatchEvent(new StorageEvent('storage',{key:'ods-theme',newValue:'unknown'})))
  expect(document.documentElement).not.toHaveAttribute('data-wallpaper')
})
