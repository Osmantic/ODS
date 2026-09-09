import { render, screen, fireEvent } from '@testing-library/react'
import PixelSelectionActions from './PixelSelectionActions'

afterEach(() => vi.restoreAllMocks())
function selection(node, end = node) {
  const value = {rangeCount:1,isCollapsed:false,toString:()=>'Exact selected text',removeAllRanges:vi.fn(),getRangeAt:()=>({startContainer:node,endContainer:end,getBoundingClientRect:()=>({left:100,top:100})})}
  vi.spyOn(window,'getSelection').mockReturnValue(value)
  return value
}
test('quotes exact selected response text into a draft', () => {
  const insert = vi.fn()
  render(<><p data-pixel-response="">Assistant answer</p><PixelSelectionActions onInsert={insert}/></>)
  const value = selection(screen.getByText('Assistant answer').firstChild)
  fireEvent.mouseUp(document)
  fireEvent.click(screen.getByRole('button',{name:'Explain'}))
  expect(insert).toHaveBeenCalledWith('Explain this passage:\n\n“Exact selected text”\n\n')
  expect(value.removeAllRanges).toHaveBeenCalledOnce()
  expect(screen.queryByRole('toolbar')).not.toBeInTheDocument()
})
test('never acts on user messages or mixed-message selections', () => {
  render(<><p>User message</p><p data-pixel-response="">Assistant answer</p><PixelSelectionActions onInsert={() => {}}/></>)
  selection(screen.getByText('User message').firstChild)
  fireEvent.mouseUp(document)
  expect(screen.queryByRole('toolbar')).not.toBeInTheDocument()
  selection(screen.getByText('Assistant answer').firstChild, screen.getByText('User message').firstChild)
  fireEvent.mouseUp(document)
  expect(screen.queryByRole('toolbar')).not.toBeInTheDocument()
})
test('Escape hides the toolbar until a new selection and switching clears it', () => {
  const view = render(<><p data-pixel-response="">Answer</p><PixelSelectionActions conversationId="one" onInsert={() => {}}/></>)
  selection(screen.getByText('Answer').firstChild)
  fireEvent.mouseUp(document)
  fireEvent.keyDown(document,{key:'Escape'})
  fireEvent.keyUp(document,{key:'Escape'})
  expect(screen.queryByRole('toolbar')).not.toBeInTheDocument()
  fireEvent.mouseUp(document)
  expect(screen.getByRole('toolbar')).toBeVisible()
  view.rerender(<><p data-pixel-response="">Answer</p><PixelSelectionActions conversationId="two" onInsert={() => {}}/></>)
  expect(screen.queryByRole('toolbar')).not.toBeInTheDocument()
})
