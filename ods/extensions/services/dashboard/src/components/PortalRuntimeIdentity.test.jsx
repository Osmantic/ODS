import {render, screen} from '@testing-library/react'
import PortalRuntimeIdentity from './PortalRuntimeIdentity'
import {readRuntimeIdentity} from '../lib/pixelRuntimeIdentity'

function observed() {
  return {schemaVersion:1, state:'partial', diskComparison:'match', runtimeMatchesRelease:null,
    reasonCode:'release-binding-unavailable', observedAt:new Date().toISOString(),
    boundary:'initialization-files-not-evaluated-code-or-release-proof',
    identities:{odsReleaseCommit:null, pixelSourceRevision:null, pluginSha256:'a'.repeat(64),
      openclawModuleSha256:'b'.repeat(64), openclawVersion:'2026.6.33', previewImageDigest:null},
    toolSchemas:{boundary:'latest-created-plugin-tools-not-offered-surface', registeredPluginToolCount:1,
      registeredPluginToolSchemasSha256:'c'.repeat(64), offeredToolCount:null, offeredToolSchemasSha256:null}}
}

it('displays available observations with explicit unknown release and offered schemas', () => {
  render(<PortalRuntimeIdentity identity={observed()} runtime={{model:'Test Model', contextLength:65536}} />)
  expect(screen.getByText('Runtime identity · Unverified')).toBeInTheDocument()
  expect(screen.getByText('a'.repeat(64))).toBeInTheDocument()
  expect(screen.getByText('65536')).toBeInTheDocument()
  expect(screen.getAllByText('Unknown')).toHaveLength(4)
  expect(screen.getByText(/do not prove the JavaScript evaluated/)).toBeInTheDocument()
})

it('shows proven disk drift without implying full release attestation', () => {
  const value = {...observed(), state:'mismatch', diskComparison:'mismatch', runtimeMatchesRelease:false, reasonCode:'runtime-files-changed'}
  render(<PortalRuntimeIdentity identity={value} />)
  expect(screen.getByText('Runtime identity · Files changed')).toBeInTheDocument()
  expect(screen.getByText(/Installed files changed/)).toBeInTheDocument()
})

it.each([undefined, null, {}, {...observed(), runtimeMatchesRelease:true},
  {...observed(), identities:{...observed().identities, pluginSha256:'/private/secret'}},
  {...observed(), toolSchemas:{...observed().toolSchemas, offeredToolCount:20}},
])('missing, malformed and forged green diagnostics stay unverified', value => {
  expect(readRuntimeIdentity(value)).toBeNull()
  render(<PortalRuntimeIdentity identity={value} />)
  expect(screen.getByText('Runtime identity · Unverified')).toBeInTheDocument()
  expect(screen.queryByText(/private\/secret/)).not.toBeInTheDocument()
})

it('does not project extra private upstream properties', () => {
  const value = observed()
  expect(readRuntimeIdentity({...value, privateToken:'secret', identities:{...value.identities, privatePath:'/secret'}}))
    .toEqual(readRuntimeIdentity(value))
})
