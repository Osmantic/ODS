import {act, fireEvent, render, screen, within} from '@testing-library/react'
import {afterEach, beforeEach, expect, test, vi} from 'vitest'
import HuggingFaceModelBrowser from './HuggingFaceModelBrowser'

const repo = {id: 'org/model', author: 'org'}
const artifacts = [
  {id: 'q4', label: 'model-Q4_K_M.gguf', quantization: 'Q4_K_M', sizeBytes: 4e9, files: []},
  {id: 'q8', label: 'model-Q8_0.gguf', quantization: 'Q8_0', sizeBytes: 8e9, files: []},
  {id: 'split', label: 'model-IQ3-00001-of-00002.gguf', quantization: 'IQ3_XS', sizeBytes: 3e9, split: true, files: [{}, {}]},
]

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn(async url => ({ok: true, json: async () => url.includes('/search?') ? {models: [repo]} : url.endsWith('/import') ? {started: true} : {...repo, artifacts}})))
})
afterEach(() => {vi.useRealTimers(); vi.unstubAllGlobals()})

async function open() {
  await act(async () => {vi.advanceTimersByTime(350)})
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: 'Choose file'}))})
  return within(screen.getByRole('dialog'))
}

test('filters the loaded artifact snapshot locally by name or quantization and recovers empty results', async () => {
  render(<HuggingFaceModelBrowser/>)
  const dialog = await open()
  const requestCount = fetch.mock.calls.length
  fireEvent.change(dialog.getByRole('searchbox', {name: 'Filter GGUF artifacts'}), {target: {value: ' q4_k_m '}})
  expect(dialog.getByText('model-Q4_K_M.gguf')).toBeVisible()
  expect(dialog.queryByText('model-Q8_0.gguf')).toBeNull()
  expect(dialog.getByRole('status')).toHaveTextContent('1 of 3 artifacts')
  fireEvent.change(dialog.getByRole('searchbox'), {target: {value: '00001-of-00002'}})
  expect(dialog.getByText('2 verified parts')).toBeVisible()
  fireEvent.change(dialog.getByRole('searchbox'), {target: {value: 'no-match'}})
  expect(dialog.getByText('No artifacts match this filter.')).toBeVisible()
  expect(dialog.queryByRole('button', {name: 'Import', exact: true})).toBeNull()
  fireEvent.click(dialog.getByRole('button', {name: 'Clear artifact filter'}))
  expect(dialog.getAllByRole('button', {name: 'Import', exact: true})).toHaveLength(3)
  expect(fetch).toHaveBeenCalledTimes(requestCount)
})

test('imports the exact filtered artifact and retains backend import guards', async () => {
  const onImportStarted = vi.fn()
  render(<HuggingFaceModelBrowser onImportStarted={onImportStarted}/>)
  const dialog = await open()
  fireEvent.change(dialog.getByRole('searchbox'), {target: {value: 'q8'}})
  await act(async () => {fireEvent.click(dialog.getByRole('button', {name: 'Import', exact: true}))})
  const request = fetch.mock.calls.find(([url]) => url.endsWith('/import'))
  expect(JSON.parse(request[1].body)).toEqual({repoId: 'org/model', artifactId: 'q8'})
  expect(onImportStarted).toHaveBeenCalledWith({started: true})
})

test('clears filtering when the operator closes and reopens a repository', async () => {
  render(<HuggingFaceModelBrowser downloadBusy/>)
  let dialog = await open()
  fireEvent.change(dialog.getByRole('searchbox'), {target: {value: 'q4'}})
  expect(dialog.getByRole('button', {name: 'Import', exact: true})).toBeDisabled()
  fireEvent.click(dialog.getByTitle('Close'))
  dialog = await open()
  expect(dialog.getByRole('searchbox')).toHaveValue('')
  expect(dialog.getAllByRole('button', {name: 'Import', exact: true})).toHaveLength(3)
})
