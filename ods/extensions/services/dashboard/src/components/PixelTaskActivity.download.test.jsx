import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import PixelTaskActivity from './PixelTaskActivity'

const task = {schemaVersion:1, runId:'chatcmpl_11111111-2222-4333-8444-555555555555', startedAt:'2026-09-08T20:00:00.000Z', finishedAt:'2026-09-08T20:00:02.000Z', state:'completed', calls:1, failures:0, blocked:0, truncated:false, activities:[{kind:'read', calls:1, failures:0, blocked:0}]}
let click
beforeEach(() => {
  vi.stubGlobal('URL', Object.assign(URL, {createObjectURL:vi.fn(() => 'blob:activity'), revokeObjectURL:vi.fn()}))
  vi.stubGlobal('fetch', vi.fn())
  click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(() => {vi.restoreAllMocks(); vi.unstubAllGlobals()})
const messages = value => [{role:'user',content:'private prompt'}, {role:'assistant',content:'private reply', task:value}]
async function exported() {
  const blob = URL.createObjectURL.mock.calls.at(-1)[0]
  return JSON.parse(await new Promise(resolve => {const reader = new globalThis.FileReader(); reader.onload = () => resolve(reader.result); reader.readAsText(blob)}))
}

it('downloads only validated runtime observations for the selected turn', async () => {
  render(<PixelTaskActivity messages={[...messages(task), ...messages(undefined)]}/>)
  expect(screen.queryByRole('button', {name:'Download activity snapshot'})).toBeNull()
  fireEvent.change(screen.getByRole('combobox', {name:'Activity turn'}), {target:{value:'1'}})
  fireEvent.click(screen.getByRole('button', {name:'Download activity snapshot'}))
  const data = await exported()
  expect(data).toEqual({schemaVersion:1, kind:'ods-task-activity', exportedAt:expect.any(String), activity:task})
  expect(JSON.stringify(data)).not.toContain('private')
  expect(fetch).not.toHaveBeenCalled()
  expect(click.mock.instances[0].download).toBe(`ods-task-${task.runId}.json`)
  expect(click.mock.instances[0].isConnected).toBe(false)
  await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:activity'), {timeout:2000})
})

it('exports the latest observed running counters without claiming completion', async () => {
  const live = {...task, state:'running', finishedAt:null}
  const view = render(<PixelTaskActivity messages={messages(live)} sending elapsed="0:01"/>)
  const newer = {...live, calls:2, activities:[{kind:'read', calls:2, failures:0, blocked:0}]}
  view.rerender(<PixelTaskActivity messages={messages(newer)} sending elapsed="0:02"/>)
  fireEvent.click(screen.getByRole('button', {name:'Download activity snapshot'}))
  expect((await exported()).activity).toEqual(newer)
  expect(screen.getByText(/Running turns are partial/)).toBeVisible()
})

it('does not offer an export for unverified metadata', () => {
  render(<PixelTaskActivity messages={messages({...task, arguments:'secret'})}/>)
  expect(screen.queryByRole('button', {name:'Download activity snapshot'})).toBeNull()
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})

it('reports a browser failure and allows an explicit retry', () => {
  click.mockImplementationOnce(() => {throw new Error('blocked')})
  render(<PixelTaskActivity messages={messages(task)}/>)
  fireEvent.click(screen.getByRole('button', {name:'Download activity snapshot'}))
  expect(screen.getByRole('alert')).toHaveTextContent('could not start')
  expect(document.querySelector('a[download]')).toBeNull()
  fireEvent.click(screen.getByRole('button', {name:'Download activity snapshot'}))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(click).toHaveBeenCalledTimes(2)
})
