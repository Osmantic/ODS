import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import PixelFileChanges from './PixelFileChanges'

const change = {
  path:'index.html', change:'modified', additions:1, deletions:1,
  diff:[
    {type:'context',oldLine:1,newLine:1,text:'<!doctype html>'},
    {type:'remove',oldLine:2,newLine:null,text:'<title>Before</title>'},
    {type:'add',oldLine:null,newLine:2,text:'<title>After</title>'},
  ],
}
async function expand(path = 'index.html') {
  fireEvent.click(screen.getByText(new RegExp(`(?:Edited|Created|Deleted) ${path.replace('.', '\\.')}`)).closest('summary'))
  return screen.findByRole('region',{name:`Changes to ${path}`})
}
afterEach(() => vi.restoreAllMocks())

it('ports the original compact disclosure, exact counts, line numbers and inert code', async () => {
  const html = '<img src=x onerror="bad()"><script>bad()</script>'
  const {container} = render(<PixelFileChanges changes={[{...change,diff:[...change.diff.slice(0,2),{...change.diff[2],text:html}]}]}/>)
  expect(screen.getByLabelText('1 lines added, 1 lines removed')).toHaveTextContent('+1-1')
  expect(screen.queryByRole('region')).toBeNull()
  const region = await expand()
  expect(region.querySelector('.artifact-diff-line.added')).toHaveAttribute('data-line','2')
  expect(region.querySelector('.artifact-diff-line.removed')).toHaveAttribute('data-line','2')
  expect(region.querySelector('.artifact-diff-line.added')).toHaveTextContent(html)
  expect(container.querySelector('img, script')).toBeNull()
  expect(within(region).queryByRole('button',{name:/Preview/})).toBeNull()
})

it('copies a real patch and only offers a preview when there is a working callback', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined), onPreview = vi.fn()
  Object.defineProperty(navigator,'clipboard',{value:{writeText},configurable:true})
  render(<PixelFileChanges changes={[change]} onPreview={onPreview}/>)
  const region = await expand()
  fireEvent.click(within(region).getByRole('button',{name:'Preview index.html'}))
  expect(onPreview).toHaveBeenCalledWith(change)
  fireEvent.click(within(region).getByRole('button',{name:'Copy changes to index.html'}))
  await waitFor(() => expect(screen.getByText('Copied')).toBeVisible())
  expect(writeText).toHaveBeenCalledWith(' <!doctype html>\n-<title>Before</title>\n+<title>After</title>')
})

it('folds only explicit contiguous unchanged context and expands those exact lines', async () => {
  const diff = [...Array.from({length:10},(_,index) => ({type:'context',oldLine:index+1,newLine:index+1,text:`context ${index+1}`})),change.diff[1],change.diff[2]]
  render(<PixelFileChanges changes={[{...change,diff}]}/>)
  const region = await expand()
  const fold = within(region).getByRole('button',{name:'7 unchanged lines'})
  expect(fold).toHaveAttribute('aria-expanded','false')
  expect(within(region).queryByText('context 1')).toBeNull()
  fireEvent.click(fold)
  expect(fold).toHaveAttribute('aria-expanded','true')
  expect(within(region).getByText('context 1')).toBeVisible()
  expect(within(region).getByText('context 10')).toBeVisible()
  fireEvent.click(fold)
  expect(within(region).queryByText('context 1')).toBeNull()
})

it('labels a gap as omitted, never fabricates unchanged lines from absent source', async () => {
  render(<PixelFileChanges changes={[{...change,diff:[change.diff[0],{...change.diff[2],newLine:99}],truncated:true}]}/>)
  const region = await expand()
  expect(within(region).getByLabelText('Omitted lines')).toBeVisible()
  expect(within(region).queryByRole('button',{name:/unchanged lines/})).toBeNull()
  expect(within(region).getByRole('status')).toHaveTextContent('Only part of this diff')
})

it('shows zero without manufacturing additions for an unchanged or empty file', async () => {
  render(<PixelFileChanges changes={[{...change,change:'created',additions:0,deletions:0,diff:[]}]}/>)
  expect(screen.getByLabelText('0 lines added, 0 lines removed')).toHaveTextContent('+0-0')
  fireEvent.click(screen.getByText('Created index.html').closest('summary'))
  expect(await screen.findByRole('status')).toHaveTextContent('No line changes.')
  expect(screen.queryByRole('button',{name:/Copy/})).toBeNull()
})

it('does not render invalid counts, malformed rows or preview links for deleted files', async () => {
  const {rerender} = render(<PixelFileChanges changes={[{...change,additions:undefined}]}/>)
  expect(screen.queryByText('+1')).toBeNull()
  fireEvent.click(screen.getByText('Edited index.html').closest('summary'))
  expect(await screen.findByRole('status')).toHaveTextContent('could not be verified')
  rerender(<PixelFileChanges changes={[{...change,change:'deleted'}]} onPreview={vi.fn()}/>)
  expect(await screen.findByRole('region',{name:'Changes to index.html'})).toBeVisible()
  expect(screen.queryByRole('button',{name:'Preview index.html'})).toBeNull()
})

it('keeps an explicit final empty added line instead of dropping it', async () => {
  render(<PixelFileChanges changes={[{...change,additions:2,diff:[...change.diff,{type:'add',oldLine:null,newLine:3,text:''}]}]}/>)
  const region = await expand()
  expect(region.querySelectorAll('.artifact-diff-line.added')).toHaveLength(2)
  expect(region.querySelectorAll('.artifact-diff-line.added')[1]).toHaveAttribute('data-line','3')
})

it('calls a first snapshot Published rather than claiming the file was created during this turn', async () => {
  render(<PixelFileChanges changes={[{...change,change:'published'}]}/>)
  expect(screen.queryByText('Created index.html')).toBeNull()
  fireEvent.click(screen.getByText('Published index.html').closest('summary'))
  expect(await screen.findByText(/No earlier snapshot was available for comparison/)).toBeVisible()
})

it('keeps unknown binary or oversized line counts unknown instead of rendering zero', async () => {
  render(<PixelFileChanges changes={[{...change,additions:null,deletions:null,diff:[]}]}/>)
  expect(screen.queryByLabelText(/lines added/)).toBeNull()
  fireEvent.click(screen.getByText('Edited index.html').closest('summary'))
  expect(await screen.findByRole('status')).toHaveTextContent('Line comparison unavailable for this file.')
})
