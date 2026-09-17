import {fireEvent, render, screen, within} from '@testing-library/react'
import PixelFileChanges from './PixelFileChanges' // eslint-disable-line no-unused-vars
import receipt from './__tests__/fixtures/published-diff-gap.json'

// Real snapshot_changes output for two publications whose middle line contains
// 300,000 unchanged characters. That row exceeds the 256 KiB diff response budget.
async function expand(change) {
  render(<PixelFileChanges changes={[change]}/>)
  fireEvent.click(screen.getByText('Edited index.html').closest('summary'))
  return screen.findByRole('region', {name:'Changes to index.html'})
}

it('marks omitted source lines even when add/remove rows alternate around the gap', async () => {
  const region = await expand(receipt.changes[0])
  expect(within(region).getAllByLabelText('Omitted lines')).toHaveLength(2)
  expect(region.querySelectorAll('.artifact-diff-line')).toHaveLength(4)
  expect(within(region).getByRole('status')).toHaveTextContent('Only part of this diff')
  expect(within(region).queryByRole('button', {name:/unchanged lines/})).toBeNull()
})

it.each(['remove', 'add'])('retains the last %s coordinate through opposite-side rows', async kind => {
  const key = kind === 'remove' ? 'oldLine' : 'newLine'
  const diff = receipt.changes[0].diff.map(row => ({...row}))
  diff[2].oldLine = 2
  diff[3].newLine = 2
  diff[kind === 'remove' ? 2 : 3][key] = 4
  const region = await expand({...receipt.changes[0], diff})
  expect(within(region).getAllByLabelText('Omitted lines')).toHaveLength(1)
})

it('does not invent gaps in consecutive replacements', async () => {
  const diff = receipt.changes[0].diff.map((row, index) => index < 2 ? row : {
    ...row, oldLine:row.oldLine === null ? null : 2, newLine:row.newLine === null ? null : 2,
  })
  const region = await expand({...receipt.changes[0], diff, truncated:false})
  expect(within(region).queryByLabelText('Omitted lines')).toBeNull()
  expect(region.querySelectorAll('.artifact-diff-line')).toHaveLength(4)
})
