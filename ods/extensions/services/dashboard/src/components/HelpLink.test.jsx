import {render, screen} from '@testing-library/react'
import HelpLink from './HelpLink'
import PortalResponseError from './PortalResponseError'
import {ODS_HELP_DISCORD_URL} from '../lib/support'

it('points at the ODS community Discord and opens it safely in a new tab', () => {
  render(<HelpLink />)
  const link = screen.getByRole('link', {name: 'Get help on Discord'})
  expect(ODS_HELP_DISCORD_URL).toBe('https://discord.gg/4ntNp9MAwC')
  expect(link).toHaveAttribute('href', ODS_HELP_DISCORD_URL)
  expect(link).toHaveAttribute('target', '_blank')
  expect(link).toHaveAttribute('rel', 'noopener noreferrer')
})

it('offers help under every Portal response failure', () => {
  render(<PortalResponseError content="Portal could not complete the response." />)
  expect(screen.getByRole('link', {name: 'Still stuck? Get help on Discord'}))
    .toHaveAttribute('href', ODS_HELP_DISCORD_URL)
})
