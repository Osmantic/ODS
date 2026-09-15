import {render, screen, fireEvent} from '@testing-library/react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import PixelReplyTable from './PixelReplyTable'

afterEach(() => {vi.restoreAllMocks(); vi.useRealTimers()})

it('exports rendered GFM cells with CSV escaping and neutralized formulas', async () => {
  let blob
  vi.spyOn(URL, 'createObjectURL').mockImplementation(value => {blob = value; return 'blob:table'})
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  let filename
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {filename = this.download})
  render(<ReactMarkdown remarkPlugins={[remarkGfm]} components={{table:PixelReplyTable}}>{'| Name | Value |\n|---|---|\n| **Việt**, "hello" | `=SUM(A1)` |\n| [Docs](https://example.com) | @cmd |\n| +start | -end |'}</ReactMarkdown>)
  expect(screen.getAllByRole('row')).toHaveLength(4)
  fireEvent.click(screen.getByRole('button', {name:'Download table CSV'}))
  const content = await new Promise((resolve, reject) => {
    const reader = new globalThis.FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = reject
    reader.readAsText(blob)
  })
  expect(content).toBe('"Name","Value"\r\n"Việt, ""hello""","\'=SUM(A1)"\r\n"Docs","\'@cmd"\r\n"\'+start","\'-end"\r\n')
  expect(filename).toBe('pixel-reply-table.csv')
  expect(blob.type).toBe('text/csv;charset=utf-8')
  expect(document.querySelector('a[download]')).toBeNull()
  await vi.waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:table'), {timeout:1500})
})

it('shows a recoverable download failure without removing the table', () => {
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => {throw new Error('unavailable')})
  render(<PixelReplyTable><tbody><tr><td>Retained</td></tr></tbody></PixelReplyTable>)
  fireEvent.click(screen.getByRole('button', {name:'Download table CSV'}))
  expect(screen.getByRole('alert')).toHaveTextContent('could not be downloaded')
  expect(screen.getByRole('cell')).toHaveTextContent('Retained')
})
