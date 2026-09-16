import { fireEvent, render, screen } from '@testing-library/react'
import PixelTextFileInput from './PixelTextFileInput'

const upload = file => fireEvent.change(screen.getByLabelText('Choose text file'), {target:{files:[file]}})
const props = {input:'Analyze this', limit:16384, disabled:false}
it('stages exact Unicode/CRLF text without sending and inserts only on confirmation', async () => {
  const insert = vi.fn()
  render(<PixelTextFileInput {...props} onInsert={insert}/>)
  const text = 'Tên,Giá\r\nTrà,12\r\n```\n'
  upload(new File([text], 'costs.csv', {type:'text/csv'}))
  await screen.findByRole('group', {name:'Review text file'})
  expect(insert).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', {name:'Insert file text'}))
  expect(insert).toHaveBeenCalledWith(`\n\nFile: "costs.csv"\n\`\`\`\`text\n${text}\n\`\`\`\`\n`)
  expect(screen.queryByRole('group')).toBeNull()
})

it.each([
  [new File(['pdf'], 'report.pdf'), /not supported/],
  [new File(['x'.repeat(16385)], 'large.txt'), /16 KB/],
  [new File([], 'empty.txt'), /nonempty/],
  [new File([new Uint8Array([255])], 'bad.txt'), /valid UTF-8/],
  [new File(['hello\0'], 'binary.txt'), /binary bytes/],
])('rejects unsupported or invalid inputs without changing the draft', async (file, message) => {
  const insert = vi.fn()
  render(<PixelTextFileInput {...props} onInsert={insert}/>)
  upload(file)
  expect(await screen.findByRole('alert')).toHaveTextContent(message)
  expect(insert).not.toHaveBeenCalled()
})

it('rechecks the current draft budget and working state before insertion', async () => {
  const insert = vi.fn()
  const {rerender} = render(<PixelTextFileInput {...props} onInsert={insert}/>)
  upload(new File(['hello'], 'a.txt'))
  await screen.findByRole('group')
  rerender(<PixelTextFileInput {...props} input={'x'.repeat(16380)} onInsert={insert}/>)
  expect(screen.getByRole('button', {name:'Insert file text'})).toBeDisabled()
  rerender(<PixelTextFileInput {...props} disabled onInsert={insert}/>)
  expect(screen.getByRole('button', {name:'Insert file text'})).toBeDisabled()
  fireEvent.click(screen.getByRole('button', {name:'Discard file'}))
  expect(insert).not.toHaveBeenCalled()
})

it('drops a pending local read when the conversation unmounts', () => {
  const insert = vi.fn(), abort = vi.fn()
  let reader
  vi.stubGlobal('FileReader', class { constructor() { reader = this } readAsArrayBuffer() {} abort = abort })
  try {
    const {unmount} = render(<PixelTextFileInput {...props} onInsert={insert}/>)
    upload(new File(['hello'], 'a.txt'))
    unmount()
    expect(abort).toHaveBeenCalledOnce()
    expect(reader.onload).toBeNull()
    expect(insert).not.toHaveBeenCalled()
  } finally { vi.unstubAllGlobals() }
})
