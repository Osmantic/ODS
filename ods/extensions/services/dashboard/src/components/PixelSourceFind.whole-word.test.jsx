import {createHash} from 'node:crypto'
import {render, screen, fireEvent} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks()})
async function show(source) {
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true, arrayBuffer:async () => new TextEncoder().encode(source).buffer})))
  const preview = {siteId:'site-'+'a'.repeat(24), entrySha256:createHash('sha256').update(source).digest('hex')}
  const view = render(<PixelPreviewSource preview={preview}/>)
  const input = await screen.findByRole('searchbox', {name:'Find in source'})
  return {...view, input}
}

it('filters substrings and identifiers, resets navigation, and keeps verified source intact', async () => {
  const source = 'card\ndiscard\ncard_title\ncard-title\nCARD\n'
  const {container, input} = await show(source)
  fireEvent.change(input, {target:{value:'card'}})
  fireEvent.click(screen.getByRole('button', {name:'Next matching line'}))
  expect(container.querySelector('[data-source-find-current]')).toHaveAttribute('data-line', '2')
  fireEvent.click(screen.getByRole('checkbox', {name:'Whole word'}))
  expect(screen.getByText('1 of 3 matching lines · Line 1')).toBeVisible()
  fireEvent.click(screen.getByRole('button', {name:'Next matching line'}))
  expect(container.querySelector('[data-source-find-current]')).toHaveAttribute('data-line', '4')
  fireEvent.click(screen.getByRole('checkbox', {name:'Match case'}))
  expect(screen.getByText('1 of 2 matching lines · Line 1')).toBeVisible()
  expect(container.querySelector('pre').textContent).toBe(source)
})

it('treats regular-expression metacharacters as literal source text', async () => {
  const {input} = await show('[a.*]\naX\n[a.*]suffix\n')
  fireEvent.change(input, {target:{value:'[a.*]'}})
  fireEvent.click(screen.getByRole('checkbox', {name:'Whole word'}))
  expect(screen.getByText('1 of 1 matching lines · Line 1')).toBeVisible()
})

it('recognizes Unicode letters, combining marks, digits and underscores at word boundaries', async () => {
  const {input} = await show('café\ndécafé\n𐐀café\ncafé2\ncafé_guide\ncafé\u0301\n(café)\n')
  fireEvent.change(input, {target:{value:'café'}})
  fireEvent.click(screen.getByRole('checkbox', {name:'Whole word'}))
  expect(screen.getByText('1 of 2 matching lines · Line 1')).toBeVisible()
  fireEvent.keyDown(input, {key:'Enter'})
  expect(screen.getByText('2 of 2 matching lines · Line 7')).toBeVisible()
  fireEvent.click(screen.getByRole('checkbox', {name:'Whole word'}))
  expect(screen.getByText('1 of 7 matching lines · Line 1')).toBeVisible()
})
