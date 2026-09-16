import {render} from '../test/test-utils'
import {screen, fireEvent} from '@testing-library/react'
import Pixel from './Pixel'
import {saveConversation} from '../lib/pixelConversations'

function publication(digit) {
  const sha256 = digit.repeat(64), siteId = 'site-' + sha256.slice(0,24)
  return {schemaVersion:1,kind:'ods-pixel-workspace-preview',relativeDirectory:'demo',siteId,sha256,
    entrySha256:'f'.repeat(64),files:1,bytes:20,port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`}
}
afterEach(() => {vi.unstubAllGlobals(); localStorage.clear()})

it('treats a republished older snapshot as the latest retained publication', async () => {
  localStorage.clear()
  const a = publication('a'), b = publication('b')
  saveConversation({schema:1,chatId:'republished',messages:[
    {role:'user',content:'Build A'}, {role:'assistant',content:'A',publication:a},
    {role:'user',content:'Change to B'}, {role:'assistant',content:'B',publication:b},
    {role:'user',content:'Restore A'}, {role:'assistant',content:'A restored',publication:a},
  ], preview:a, workspaceOpen:true})
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({available:true,model:'pixel/default'})})))
  render(<Pixel/> )
  await screen.findByText('Available')
  const selector = screen.getByLabelText('Published version')
  expect([...selector.querySelectorAll('option')].map(option => option.value)).toEqual([b.siteId,a.siteId])
  expect(screen.queryByRole('button',{name:'Show latest publication'})).toBeNull()
  fireEvent.change(selector,{target:{value:b.siteId}})
  fireEvent.click(screen.getByRole('button',{name:'Show latest publication'}))
  expect(screen.getByTitle('Interactive Portal preview')).toHaveAttribute('src',`/pixel-preview/${a.siteId}/__ods_view__.html`)
  expect(fetch.mock.calls.some(([url]) => url === '/api/pixel/chat/stream')).toBe(false)
})
