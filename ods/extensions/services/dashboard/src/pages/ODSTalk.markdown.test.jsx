import { fireEvent, screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import ODSTalk from './ODSTalk'

afterEach(() => vi.unstubAllGlobals())

test('renders inline commands, fenced code and GFM structures from a Talk reply', async () => {
  const text = 'Run `ods doctor` first.\n\n```bash\nods status\n```\n\n| Service | State |\n| --- | --- |\n| LLM | Ready |\n\n- [x] Verified\n\n~~Obsolete~~'
  const frames = [{type:'complete', text, status:'ok'}, {type:'done'}]
  const reader = {read:async () => frames.length ? {done:false,value:new TextEncoder().encode(`data: ${JSON.stringify(frames.shift())}\n\n`)} : {done:true}, cancel:async()=>{},releaseLock:()=>{}}
  vi.stubGlobal('fetch', vi.fn(async url => url === '/api/talk/status'
    ? {ok:true,json:async()=>({capabilities:{text_chat:true}})}
    : {ok:true,body:{getReader:()=>reader}}))
  render(<ODSTalk />)
  await screen.findByText('Ready')
  fireEvent.change(screen.getByPlaceholderText('Message ODS'),{target:{value:'Show diagnostics'}})
  fireEvent.click(screen.getByRole('button',{name:'Send message'}))
  const inline = await screen.findByText('ods doctor')
  expect(inline.tagName).toBe('CODE')
  expect(inline).not.toHaveClass('block')
  expect(screen.getByText('ods status').closest('pre')).toBeInTheDocument()
  expect(screen.getByRole('table')).toHaveTextContent('LLMReady')
  expect(screen.getByRole('checkbox')).toBeChecked()
  expect(screen.getByRole('checkbox')).toBeDisabled()
  expect(screen.getByText('Obsolete').tagName).toBe('DEL')
})
