import {fireEvent, render, screen, within} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import Settings from './Settings'
import {useTheme} from '../contexts/ThemeContext'
vi.mock('../contexts/ThemeContext',()=>({useTheme:vi.fn()}))
vi.mock('../components/settings/PixelProviderSettings',()=>({default:()=>null}))
vi.mock('../components/settings/PixelRuntimeSettings',()=>({default:()=>null}))
vi.mock('../components/settings/PixelSharingSettings',()=>({default:()=>null}))
vi.mock('../components/settings/PixelAccessCard',()=>({default:()=>null}))
vi.mock('../components/settings/EnvEditor',()=>({default:()=>null}))

const image='custom-'+'a'.repeat(36), video='custom-'+'b'.repeat(36)
let themeState
beforeEach(()=>{
  themeState={theme:'ods', themes:['ods','ocean',image,video], labels:{ods:'ODS',ocean:'Ocean', [image]:'My Ocean', [video]:'Rain'}, wallpapers:[{id:'ods'},{id:'ocean'},{id:image,kind:'image'},{id:video,kind:'video'}], setTheme:vi.fn()}
  useTheme.mockImplementation(()=>themeState)
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({})})))
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})
function show(){return render(<MemoryRouter><Settings activeSection="appearance"/></MemoryRouter>)}
const gallery=()=>within(screen.getByLabelText('Workspace themes'))

it('combines name and source filters without selecting or persisting another wallpaper',async()=>{
  show()
  await screen.findByRole('searchbox',{name:'Search wallpapers'})
  fireEvent.change(screen.getByRole('searchbox',{name:'Search wallpapers'}),{target:{value:' OCEAN '}})
  expect(gallery().getAllByRole('button')).toHaveLength(2)
  fireEvent.change(screen.getByRole('combobox',{name:'Wallpaper source'}),{target:{value:'custom'}})
  expect(gallery().getAllByRole('button')).toHaveLength(1)
  expect(gallery().getByRole('button',{name:'My Ocean'})).toBeVisible()
  expect(themeState.setTheme).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:'Clear wallpaper filters'}))
  expect(gallery().getAllByRole('button')).toHaveLength(4)
  expect(gallery().getByRole('button',{name:'ODS'})).toHaveAttribute('aria-pressed','true')
})

it('filters built-ins and videos and preserves the existing explicit selection action',async()=>{
  show()
  await screen.findByRole('searchbox',{name:'Search wallpapers'})
  const source=screen.getByRole('combobox',{name:'Wallpaper source'})
  fireEvent.change(source,{target:{value:'builtin'}})
  expect(gallery().getAllByRole('button')).toHaveLength(2)
  fireEvent.change(source,{target:{value:'video'}})
  expect(gallery().getAllByRole('button')).toHaveLength(1)
  fireEvent.click(gallery().getByRole('button',{name:'Rain'}))
  expect(themeState.setTheme).toHaveBeenCalledExactlyOnceWith(video)
})

it('shows an honest empty result and recomputes the filtered gallery after library refresh',async()=>{
  const view=show()
  await screen.findByRole('searchbox',{name:'Search wallpapers'})
  fireEvent.change(screen.getByRole('searchbox',{name:'Search wallpapers'}),{target:{value:'new'}})
  expect(screen.getByText('No wallpapers match these filters.')).toBeVisible()
  expect(screen.getByText('Showing 0 of 4 wallpapers')).toBeVisible()
  const added='custom-'+'c'.repeat(36)
  themeState={...themeState,themes:[...themeState.themes,added],labels:{...themeState.labels,[added]:'New photo'},wallpapers:[...themeState.wallpapers,{id:added,kind:'image'}]}
  view.rerender(<MemoryRouter><Settings activeSection="appearance"/></MemoryRouter>)
  expect(gallery().getByRole('button',{name:'New photo'})).toBeVisible()
  expect(screen.getByText('Showing 1 of 5 wallpapers')).toBeVisible()
  expect(themeState.setTheme).not.toHaveBeenCalled()
})
