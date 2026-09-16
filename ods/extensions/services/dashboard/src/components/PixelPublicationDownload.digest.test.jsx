import {createHash} from 'node:crypto'
import {fireEvent, render, screen} from '@testing-library/react'
import PixelTaskFiles from './PixelTaskFiles'
import fixture from '../test/fixtures/publication-digest.json'

let manifest, contents
beforeEach(() => {
  manifest = JSON.parse(JSON.stringify(fixture.manifest))
  contents = {...fixture.contents}
  vi.stubGlobal('fetch', vi.fn(async url => {
    const value = url.endsWith('__ods_manifest__.json') ? JSON.stringify(manifest) : contents[url.split('/').slice(3).join('/') || 'index.html']
    return {ok:true, arrayBuffer:async () => new TextEncoder().encode(value).buffer}
  }))
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:publication')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(() => {vi.restoreAllMocks(); vi.unstubAllGlobals()})
async function download() {
  render(<PixelTaskFiles preview={fixture.preview}/>)
  await screen.findByText('index.html')
  fireEvent.click(screen.getByRole('button', {name:'Download publication ZIP'}))
}

it('accepts an actual Python publisher receipt including empty assets and reordered manifest entries', async () => {
  manifest.files.reverse()
  await download()
  await screen.findByText('Publication download started.')
  expect(URL.createObjectURL).toHaveBeenCalledOnce()
})

it('rejects changed support bytes even when the manifest supplies matching per-file hashes', async () => {
  contents['assets/site.css'] = 'a{b:d;}'
  manifest.files.find(file => file.path === 'assets/site.css').sha256 = createHash('sha256').update(contents['assets/site.css']).digest('hex')
  await download()
  expect(await screen.findByRole('alert')).toHaveTextContent('complete publication could not be verified')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})

it('binds filenames as well as contents to the original publication', async () => {
  manifest.files.find(file => file.path === 'assets/site.css').path = 'assets/other.css'
  contents['assets/other.css'] = contents['assets/site.css']
  await download()
  expect(await screen.findByRole('alert')).toHaveTextContent('complete publication could not be verified')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})
