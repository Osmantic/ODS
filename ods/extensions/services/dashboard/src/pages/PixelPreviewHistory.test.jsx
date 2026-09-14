import {render} from '../test/test-utils'
import {screen,fireEvent,waitFor} from '@testing-library/react'
import Pixel from './Pixel'
import {saveConversation} from '../lib/pixelConversations'

function publication(digit) {
  const sha256=digit.repeat(64),siteId='site-'+sha256.slice(0,24)
  return {schemaVersion:1,kind:'ods-pixel-workspace-preview',relativeDirectory:'demo',siteId,sha256,entrySha256:'f'.repeat(64),files:1,bytes:20,port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`}
}
const first=publication('a'), latest=publication('b')
beforeEach(()=>{
  localStorage.clear()
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({available:true,model:'pixel/default'}),arrayBuffer:async()=>new TextEncoder().encode('{}').buffer})))
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})
function seed(extra=[]) {
  saveConversation({schema:1,chatId:'versions',messages:[{role:'user',content:'Build a page'},{role:'assistant',content:'First result',publication:first},{role:'user',content:'Improve it'},{role:'assistant',content:'Second result',publication:latest,beforePublication:first},...extra],preview:latest,workspaceOpen:true})
}
it('opens an earlier verified publication and returns to latest without submitting work',async()=>{
  seed();render(<Pixel/>);await screen.findByText('Available')
  const selector=screen.getByLabelText('Published version')
  expect(selector).toHaveValue(latest.siteId)
  fireEvent.change(selector,{target:{value:first.siteId}})
  expect(screen.getByTitle('Interactive Portal preview')).toHaveAttribute('src',`/pixel-preview/${first.siteId}/__ods_view__.html`)
  expect(screen.getByText(/Workspace files are unchanged/)).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Show latest publication'}))
  expect(screen.getByTitle('Interactive Portal preview')).toHaveAttribute('src',`/pixel-preview/${latest.siteId}/__ods_view__.html`)
  expect(fetch.mock.calls.some(([url])=>url==='/api/pixel/chat/stream')).toBe(false)
  await waitFor(()=>expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).messages).toHaveLength(4))
})
it('deduplicates retained snapshots and excludes malformed publication metadata',async()=>{
  seed([{role:'assistant',content:'Repeated',publication:latest},{role:'assistant',content:'Invalid',publication:{...publication('c'),url:'https://invalid.example/'}}])
  render(<Pixel/>);await screen.findByText('Available')
  const options=screen.getByLabelText('Published version').querySelectorAll('option')
  expect([...options].map(option=>option.value)).toEqual([first.siteId,latest.siteId])
})
