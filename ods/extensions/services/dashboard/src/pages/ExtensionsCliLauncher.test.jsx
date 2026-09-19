import {screen} from '@testing-library/react'
import {render} from '../test/test-utils'
import Extensions from './Extensions'

afterEach(() => vi.unstubAllGlobals())

it('does not advertise a generic Compose command that invokes the Aider echo stub', async () => {
  vi.stubGlobal('fetch', vi.fn(async url => ({ok:true, json:async () => String(url).includes('/api/templates')
    ? {templates:[]}
    : {agent_available:true, extensions:[{id:'aider', name:'Aider', source:'user', status:'cli_installed', features:[], description:'Terminal coding assistant'}]}})))
  render(<Extensions compact/>)
  await screen.findByText('Aider')
  expect(screen.getByText('CLI tool installed — launch it from the ODS terminal')).toBeInTheDocument()
  expect(screen.queryByText(/docker compose run --rm/)).not.toBeInTheDocument()
})
