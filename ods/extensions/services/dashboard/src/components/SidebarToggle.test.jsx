import {render, screen, fireEvent} from '@testing-library/react'
import {MemoryRouter, useLocation} from 'react-router-dom'
import Sidebar from './Sidebar'
vi.mock('./ODSLogo',()=>({default:()=>null}))
vi.mock('./PixelConversationNavigation',()=>({default:()=>null}))
vi.mock('../plugins/registry',()=>({getSidebarExternalLinks:()=>[],getSidebarNavItems:()=>['Dashboard','Models','Extensions','Settings'].map(label=>({label,path:`/${label.toLowerCase()}`,icon:()=>null}))}))
function Location(){return <output data-testid="location">{useLocation().pathname}</output>}
beforeEach(()=>vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>[]}))))
afterEach(()=>vi.unstubAllGlobals())
it.each(['Dashboard','Models','Extensions','Settings'])('toggles %s open and closed from the navigation',label=>{
  render(<MemoryRouter><Sidebar status={{}}/><Location/></MemoryRouter>)
  const link=screen.getByRole('link',{name:label,exact:true})
  fireEvent.click(link)
  expect(screen.getByTestId('location')).toHaveTextContent(`/${label.toLowerCase()}`)
  fireEvent.click(link)
  expect(screen.getByTestId('location').textContent).toBe('/')
})
it('switches panels directly and leaves modified clicks to the browser',()=>{
  render(<MemoryRouter initialEntries={['/models']}><Sidebar status={{}}/><Location/></MemoryRouter>)
  fireEvent.click(screen.getByRole('link',{name:'Models'}),{ctrlKey:true})
  expect(screen.getByTestId('location')).toHaveTextContent('/models')
  fireEvent.click(screen.getByRole('link',{name:'Extensions'}))
  expect(screen.getByTestId('location')).toHaveTextContent('/extensions')
})
