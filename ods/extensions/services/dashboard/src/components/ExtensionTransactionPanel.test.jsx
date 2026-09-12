import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ExtensionTransactionPanel from './ExtensionTransactionPanel'
import {
  approveExtensionTransaction,
  configureExtensionTransaction,
  executeExtensionTransaction,
  getExtensionTransaction,
  getExtensionTransactionConfiguration,
} from '../lib/extensionTransactions'

vi.mock('../lib/extensionTransactions', () => ({
  approveExtensionTransaction: vi.fn(),
  configureExtensionTransaction: vi.fn(),
  executeExtensionTransaction: vi.fn(),
  getExtensionTransaction: vi.fn(),
  getExtensionTransactionConfiguration: vi.fn(),
}))

const HASH = 'a'.repeat(64)
const TX_ID = `txn-${'b'.repeat(24)}`
const plan = {
  schema: 'ods.assistant-first.plan.v1',
  validUntil: '2099-09-13T00:00:00Z',
  requestedServices: ['notes'],
  selectedServices: ['database', 'notes'],
  operations: [
    { serviceId: 'database', action: 'install' },
    { serviceId: 'notes', action: 'install' },
  ],
  definitions: [
    {
      id: 'database', version: '1.0.0', dependsOn: [], provides: [], requires: [], conflicts: [],
      resources: { hostPorts: [{ port: 5432, protocol: 'tcp' }], volumes: ['notes-db'], networks: ['notes'], devices: [], hostPermissions: ['network'], linuxCapabilities: [] },
      trust: { tier: 'bundled', publisher: 'ODS' },
    },
    {
      id: 'notes', version: '2.0.0', dependsOn: ['database'], provides: [], requires: [], conflicts: [],
      resources: { hostPorts: [], volumes: [], networks: [], devices: [], hostPermissions: [], linuxCapabilities: [] },
      trust: { tier: 'bundled', publisher: 'ODS' },
    },
  ],
  resourceDelta: { downloadBytes: 1024, diskBytes: 2048, ramBytes: 4096, vramBytes: 0, cpuMillicores: 250, gpuCount: 0 },
  requiredConfigKeys: ['NOTES_PATH'],
  requiredSecretKeys: ['NOTES_TOKEN'],
  dataEffects: [{ serviceId: 'database', paths: ['/data'] }],
  rollbackEffects: [{ serviceId: 'database', contract: 'definition' }],
  warnings: [{ code: 'estimated-ramBytes-exceeds-available' }],
}

const proposal = {
  schema: 'ods.assistant-first.transaction-proposal.v1',
  transactionId: TX_ID,
  planHash: HASH,
  state: 'awaiting_approval',
  sequence: 2,
  envelope: { plan },
}

const status = (state = 'awaiting_approval') => ({
  schema: 'ods.assistant-first.transaction-status.v1',
  transactionId: TX_ID,
  planHash: HASH,
  state,
  sequence: state === 'approved' ? 3 : state === 'committed' ? 10 : 2,
  plan,
  journal: [{ sequence: 1, state: 'planned' }, { sequence: 2, state }],
  approval: { approved: state !== 'awaiting_approval' },
})

const configuration = (configured = false) => ({
  schema: 'ods.assistant-first.transaction-configuration-view.v1',
  transactionId: TX_ID,
  planHash: HASH,
  schemaHash: 'c'.repeat(64),
  fields: [
    { key: 'NOTES_PATH', type: 'string', required: true, secret: false, source: 'user', restartBehavior: 'service' },
    { key: 'NOTES_TOKEN', type: 'string', required: true, secret: true, source: 'user', restartBehavior: 'service', validation: { minLength: 8 } },
  ],
  configured,
  values: configured ? { NOTES_PATH: '/notes' } : {},
  presentConfigKeys: configured ? ['NOTES_PATH'] : [],
  presentSecretKeys: configured ? ['NOTES_TOKEN'] : [],
  appliedDefaultKeys: [],
})

beforeEach(() => {
  vi.clearAllMocks()
  getExtensionTransaction.mockResolvedValue(status())
  getExtensionTransactionConfiguration.mockResolvedValue(configuration())
  configureExtensionTransaction.mockResolvedValue(configuration(true))
  approveExtensionTransaction.mockResolvedValue({ transactionId: TX_ID, state: 'approved', sequence: 3 })
  executeExtensionTransaction.mockResolvedValue({ transactionId: TX_ID, planHash: HASH, finalState: 'committed', sequence: 10, appliedServices: ['database', 'notes'], error: null })
})

describe('ExtensionTransactionPanel', () => {
  it('renders the exact plan, dependency impact, trust, warning, and a non-success approval state', async () => {
    render(<ExtensionTransactionPanel proposal={proposal} extensionName="Notes" onClose={() => {}} />)
    const badge = await screen.findByRole('status')
    expect(badge).toHaveTextContent('Awaiting approval')
    expect(badge.className).toContain('amber')
    expect(badge.className).not.toContain('green')
    expect(screen.getByText(HASH)).toBeVisible()
    expect(screen.getByText('Dependency')).toBeVisible()
    expect(screen.getByText('Required by notes')).toBeVisible()
    expect(screen.getAllByText(/bundled trust/)).toHaveLength(2)
    expect(screen.getByText('5432/tcp')).toBeVisible()
    expect(screen.getByText('estimated-ramBytes-exceeds-available')).toBeVisible()
    expect(screen.queryByText(/succeeded/i)).toBeNull()
  })

  it('keeps secrets out of React state, separates submission channels, and clears the password field', async () => {
    render(<ExtensionTransactionPanel proposal={proposal} extensionName="Notes" onClose={() => {}} />)
    const path = await screen.findByLabelText(/NOTES_PATH/)
    const secret = screen.getByLabelText(/NOTES_TOKEN/)
    expect(secret).toHaveAttribute('type', 'password')
    expect(secret).toHaveAttribute('autocomplete', 'new-password')
    fireEvent.change(path, { target: { value: '/notes' } })
    fireEvent.change(secret, { target: { value: 'private-token' } })
    fireEvent.click(screen.getByRole('button', { name: 'Validate and secure configuration' }))
    await waitFor(() => expect(configureExtensionTransaction).toHaveBeenCalledWith(
      TX_ID,
      HASH,
      'c'.repeat(64),
      { NOTES_PATH: '/notes' },
      { NOTES_TOKEN: 'private-token' },
    ))
    await waitFor(() => expect(secret).toHaveValue(''))
    expect(screen.queryByText('private-token')).toBeNull()
    expect(screen.getByText(/Host-held secrets present: NOTES_TOKEN/)).toBeVisible()
  })

  it('requires secured configuration, then binds owner approval and executes only the same hash', async () => {
    getExtensionTransaction
      .mockResolvedValueOnce(status())
      .mockResolvedValueOnce(status('approved'))
      .mockResolvedValueOnce(status('committed'))
    const onCommitted = vi.fn()
    render(<ExtensionTransactionPanel proposal={proposal} extensionName="Notes" onClose={() => {}} onCommitted={onCommitted} />)
    const approve = await screen.findByRole('button', { name: 'Approve exact plan' })
    expect(approve).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/NOTES_PATH/), { target: { value: '/notes' } })
    fireEvent.change(screen.getByLabelText(/NOTES_TOKEN/), { target: { value: 'private-token' } })
    fireEvent.click(screen.getByRole('button', { name: 'Validate and secure configuration' }))
    await waitFor(() => expect(approve).toBeEnabled())
    fireEvent.click(approve)
    await waitFor(() => expect(approveExtensionTransaction).toHaveBeenCalledWith(TX_ID, HASH))
    const apply = await screen.findByRole('button', { name: 'Apply approved plan' })
    fireEvent.click(apply)
    await waitFor(() => expect(executeExtensionTransaction).toHaveBeenCalledWith(TX_ID, HASH))
    await waitFor(() => expect(onCommitted).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('status')).toHaveTextContent('Succeeded')
  })

  it('allows a required boolean field to be explicitly submitted as false', async () => {
    const booleanConfiguration = {
      ...configuration(),
      fields: [{ key: 'ENABLE_REMOTE', type: 'boolean', required: true, secret: false, source: 'user', restartBehavior: 'service' }],
    }
    getExtensionTransactionConfiguration.mockResolvedValue(booleanConfiguration)
    configureExtensionTransaction.mockResolvedValue({
      ...booleanConfiguration,
      configured: true,
      values: { ENABLE_REMOTE: false },
      presentConfigKeys: ['ENABLE_REMOTE'],
    })
    render(<ExtensionTransactionPanel proposal={{...proposal,envelope:{plan:{...plan,requiredConfigKeys:['ENABLE_REMOTE'],requiredSecretKeys:[]}}}} extensionName="Notes" onClose={() => {}} />)
    const checkbox = await screen.findByLabelText(/ENABLE_REMOTE/)
    expect(checkbox).not.toBeChecked()
    expect(checkbox).not.toHaveAttribute('required')
    fireEvent.click(screen.getByRole('button', { name: 'Validate and secure configuration' }))
    await waitFor(() => expect(configureExtensionTransaction).toHaveBeenCalledWith(
      TX_ID,
      HASH,
      'c'.repeat(64),
      { ENABLE_REMOTE: false },
      {},
    ))
  })

  it('guards execution synchronously against a double click', async () => {
    const approved = status('approved')
    getExtensionTransaction.mockResolvedValue(approved)
    getExtensionTransactionConfiguration.mockResolvedValue({ ...configuration(), fields: [] })
    let finishExecution
    executeExtensionTransaction.mockReturnValue(new Promise(resolve => { finishExecution = resolve }))
    render(<ExtensionTransactionPanel proposal={{...proposal,state:'approved'}} extensionName="Notes" onClose={() => {}} />)
    const apply = await screen.findByRole('button', { name: 'Apply approved plan' })
    fireEvent.click(apply)
    fireEvent.click(apply)
    expect(executeExtensionTransaction).toHaveBeenCalledTimes(1)
    getExtensionTransaction.mockResolvedValue(status('committed'))
    finishExecution({ transactionId: TX_ID, planHash: HASH, finalState: 'committed', sequence: 10, appliedServices: ['notes'], error: null })
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Succeeded'))
  })

  it('blocks expired approval in the browser while the server remains authoritative', async () => {
    const expiredPlan = { ...plan, validUntil: '2020-01-01T00:00:00Z', requiredConfigKeys: [], requiredSecretKeys: [] }
    getExtensionTransaction.mockResolvedValue({ ...status(), plan: expiredPlan })
    getExtensionTransactionConfiguration.mockResolvedValue({ ...configuration(), fields: [] })
    render(<ExtensionTransactionPanel proposal={{...proposal,envelope:{plan:expiredPlan}}} extensionName="Notes" onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Approve exact plan' }))
    expect(approveExtensionTransaction).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('plan has expired')
  })

  it('closes with Escape only while no operation is in flight', async () => {
    const onClose = vi.fn()
    render(<ExtensionTransactionPanel proposal={proposal} extensionName="Notes" onClose={onClose} />)
    const dialog = await screen.findByRole('dialog')
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
