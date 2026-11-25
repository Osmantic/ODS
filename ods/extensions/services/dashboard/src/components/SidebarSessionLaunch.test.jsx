import {render, screen, fireEvent, waitFor} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import Sidebar from './Sidebar'
vi.mock('./ODSLogo',()=>({default:()=>null}))
vi.mock('./PixelConversationNavigation',()=>({default:()=>null}))
vi.mock('../plugins/registry',()=>({getSidebarNavItems:()=>[],getSidebarExternalLinks:()=>[
  {key:'hermes-proxy',label:'Hermes',icon:()=>null,healthy:true,url:'http://localhost:9120/auth/ods'},
  {key:'n8n',label:'n8n',icon:()=>null,healthy:true,url:'http://localhost:5678'},
]}))

beforeEach(()=>vi.stubGlobal('fetch',vi.fn(async url=>url==='/api/auth/verify-session'
  ? {ok:true,json:async()=>({valid:true,expires_at:Math.floor(Date.now()/1000)+6*3600})}
  : {ok:true,json:async()=>[]})))
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals()})

it('re-checks the ODS session before Applications opens Hermes, and leaves other apps alone',async()=>{
  const tab={opener:window,closed:false,location:{replace:vi.fn()}}
  vi.spyOn(window,'open').mockReturnValue(tab)
  render(<MemoryRouter><Sidebar status={{}}/></MemoryRouter>)

  const otherApp=screen.getByRole('link',{name:'n8n'})
  otherApp.addEventListener('click',event=>event.preventDefault())
  fireEvent.click(otherApp)
  expect(window.open).not.toHaveBeenCalled()

  const hermes=screen.getByRole('link',{name:'Hermes'})
  expect(hermes).toHaveAttribute('href','http://localhost:9120/auth/ods')
  expect(fireEvent.click(hermes)).toBe(false)
  expect(window.open).toHaveBeenCalledWith('','_blank')
  await waitFor(()=>expect(tab.location.replace).toHaveBeenCalledWith('http://localhost:9120/auth/ods'))
  expect(fetch).toHaveBeenCalledWith('/api/auth/verify-session',expect.objectContaining({credentials:'same-origin'}))
})
