import {fireEvent, render, screen} from '@testing-library/react'
import UsageView from './UsageView'

afterEach(() => {vi.restoreAllMocks();vi.unstubAllGlobals()})

async function exportRow(row) {
  const createObjectURL = vi.fn(() => 'blob:usage')
  vi.stubGlobal('URL', {createObjectURL, revokeObjectURL:vi.fn()})
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  render(<UsageView report={{source:{status:'ok'},models:[row]}}
    readiness={{status:'ready'}} range={{start:'2026-09-01'}}/>)
  fireEvent.click(screen.getByRole('button', {name:'Models', exact:true}))
  fireEvent.click(screen.getByRole('button', {name:'Export CSV'}))
  const blob = createObjectURL.mock.calls[0][0]
  return new Promise((resolve, reject) => {
    const reader = new globalThis.FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(reader.error)
    reader.readAsText(blob)
  })
}

it.each(['=', '+', '-', '@', '\t', '\r', '\n', '＝', '＋', '－', '＠'])(
  'exports metadata beginning with %j as quoted text',
  async prefix => {
    const value = prefix + 'SUM(1,2)"'
    const csv = await exportRow({model:value,provider:value,service:value,input_tokens:1})
    expect(csv.split('\r\n')[0]).toMatch(/^model,provider,service,/)
    const expectedCell = '"' + "'" + value.replaceAll('"', '""') + '"'
    expect(csv.slice(csv.indexOf('\r\n') + 2).startsWith(
      [expectedCell, expectedCell, expectedCell, '"1"'].join(','),
    )).toBe(true)
  },
)

it('preserves ordinary Unicode, embedded newlines, commas and quotes', async () => {
  const csv = await exportRow({model:'模型\nv2',provider:'a,b',service:'say "hello"',input_tokens:1})
  expect(csv).toContain('"模型\nv2","a,b","say ""hello""","1"')
})
