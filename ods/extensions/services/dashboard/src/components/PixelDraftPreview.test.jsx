import {render,screen,fireEvent} from '@testing-library/react'
import PixelDraftPreview from './PixelDraftPreview'

it('previews headings, lists, code and GFM tables while preserving the draft source',()=>{
  const input='# Plan\n\n- One\n- Two\n\n```js\nconst x = 1\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |'
  render(<PixelDraftPreview input={input}/>)
  fireEvent.click(screen.getByRole('button',{name:'Preview draft'}))
  expect(screen.getByRole('heading',{name:'Plan',level:1})).toBeVisible()
  expect(screen.getAllByRole('listitem')).toHaveLength(2)
  expect(screen.getByRole('table')).toBeVisible()
  expect(screen.getByText('const x = 1')).toBeVisible()
  expect(input).toContain('```js')
})
it('does not fetch images, activate links or execute raw HTML',()=>{
  const {container}=render(<PixelDraftPreview input={'![photo](https://example.com/private.png)\n[link](https://example.com)\n<script>bad()</script>'}/>)
  fireEvent.click(screen.getByRole('button',{name:'Preview draft'}))
  expect(screen.getByText('[Image: photo]')).toBeVisible()
  expect(container.querySelector('img')).toBeNull()
  expect(container.querySelector('a')).toBeNull()
  expect(container.querySelector('script')).toBeNull()
})
it('updates an open preview and allows closing it after the draft is cleared',()=>{
  const {rerender}=render(<PixelDraftPreview input='**Original**'/>)
  fireEvent.click(screen.getByRole('button',{name:'Preview draft'}))
  rerender(<PixelDraftPreview input='## Updated'/> )
  expect(screen.getByRole('heading',{name:'Updated'})).toBeVisible()
  expect(screen.queryByText('Original')).toBeNull()
  rerender(<PixelDraftPreview input=''/> )
  expect(screen.getByText('Your draft is empty.')).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Hide draft preview'}))
  expect(screen.queryByRole('region')).toBeNull()
  expect(screen.getByRole('button',{name:'Preview draft'})).toBeDisabled()
})
