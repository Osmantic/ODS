import {fireEvent, screen} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,json:async () => ({available:true,model:'pixel/default'})})))
})
afterEach(() => {vi.unstubAllGlobals();vi.restoreAllMocks()})

it('keeps one file input and draft preview across composer updates and chat switches', async () => {
  render(<Pixel/>)
  await screen.findByText('Available')
  for (const value of ['One', 'Two\nlines', 'Three', '']) {
    fireEvent.change(screen.getByPlaceholderText('Message Portal...'), {target:{value}})
    expect(screen.getAllByRole('button', {name:'Add text file',exact:true})).toHaveLength(1)
    expect(screen.getAllByRole('button', {name:'Preview draft',exact:true})).toHaveLength(1)
  }
  fireEvent(window, new Event('ods:pixel-new-task'))
  expect(screen.getAllByRole('button', {name:'Add text file',exact:true})).toHaveLength(1)
})
