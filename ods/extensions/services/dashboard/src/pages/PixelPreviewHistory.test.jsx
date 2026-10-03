// Conversation/review state tests isolate the asynchronous origin handshake.
// Its real transport, timeout and stale-receipt behavior is covered in previewOrigin.test.jsx.
vi.mock('../lib/useVerifiedPreview',()=>({default:(_preview,access)=>access}))
import {render} from '../test/test-utils'
import {screen,waitFor} from '@testing-library/react'
import Pixel from './Pixel'
import {saveConversation} from '../lib/pixelConversations'
import {previewManifestResponse} from '../test/previewFixtures'

function publication(digit) {
  const sha256=digit.repeat(64),siteId='site-'+sha256.slice(0,24)
  return {schemaVersion:1,kind:'ods-pixel-workspace-preview',relativeDirectory:'demo',siteId,sha256,entrySha256:'f'.repeat(64),files:1,bytes:20,port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`}
}
const first=publication('a'), latest=publication('b')
beforeEach(()=>{
  localStorage.clear()
  vi.stubGlobal('fetch',vi.fn(async url=>url.includes('__ods_manifest__')?previewManifestResponse(url.includes(latest.siteId)?latest:first):({ok:true,json:async()=>({available:true,model:'pixel/default'}),arrayBuffer:async()=>new TextEncoder().encode('{}').buffer})))
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})
function seed(extra=[]) {
  saveConversation({schema:1,chatId:'versions',messages:[{role:'user',content:'Build a page'},{role:'assistant',content:'First result',publication:first},{role:'user',content:'Improve it'},{role:'assistant',content:'Second result',publication:latest,beforePublication:first},...extra],preview:latest,workspaceOpen:true})
}
it('shows the latest verified project publication without a historical selector or submitting work',async()=>{
  seed();render(<Pixel/>);await screen.findByText('Available')
  expect(screen.queryByLabelText('Published version')).toBeNull()
  expect(await screen.findByTitle('Interactive Portal preview')).toHaveAttribute('src',`/pixel-preview/${latest.siteId}/__ods_view__.html`)
  expect(fetch.mock.calls.some(([url])=>url==='/api/pixel/chat/stream')).toBe(false)
  await waitFor(()=>expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).messages).toHaveLength(4))
})
it('deduplicates retained snapshots and excludes malformed publication metadata',async()=>{
  seed([{role:'assistant',content:'Repeated',publication:latest},{role:'assistant',content:'Invalid',publication:{...publication('c'),url:'https://invalid.example/'}}])
  render(<Pixel/>);await screen.findByText('Available')
  expect(screen.queryByLabelText('Published version')).toBeNull()
  expect(await screen.findByTitle('Interactive Portal preview')).toHaveAttribute('src',`/pixel-preview/${latest.siteId}/__ods_view__.html`)
})

// These suites exercise conversation/publication selection, not manifest transport.
// Workspace and artifact suites cover missing, corrupt and delayed manifests.
vi.mock('../lib/pixelArtifacts',async importOriginal=>({
  ...await importOriginal(),
  loadSnapshotFiles:vi.fn(async preview=>[{path:'index.html',bytes:preview.bytes,sha256:preview.entrySha256}]),
}))
