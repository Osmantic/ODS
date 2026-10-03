import { describe, it, expect } from 'vitest'
import { getInferenceMode, isRemoteInference } from './inferenceMode'

describe('getInferenceMode', () => {
  it('treats remote-provider source as remote even with local GPU telemetry', () => {
    const status = {
      inferenceMode: 'remote',
      inferenceSource: 'remote-provider',
      gpu: { gpu_count: 2, name: 'Intel Arc' },
    }
    const mode = getInferenceMode(status)
    expect(mode.isRemote).toBe(true)
    expect(mode.isCloud).toBe(false)
    expect(mode.isLocal).toBe(false)
    expect(isRemoteInference(status)).toBe(true)
  })

  it('treats cloud mode as remote inference', () => {
    const status = { inferenceMode: 'cloud', inferenceSource: 'cloud-mode', gpu: { gpu_count: 3 } }
    expect(getInferenceMode(status).isCloud).toBe(true)
    expect(isRemoteInference(status)).toBe(true)
  })

  it('source remote-provider overrides a configured local mode', () => {
    const status = { inferenceMode: 'local', inferenceSource: 'remote-provider' }
    expect(getInferenceMode(status).isRemote).toBe(true)
  })

  it('keeps local multi-GPU behavior unchanged', () => {
    const status = { inferenceMode: 'local', inferenceSource: 'local-runtime', gpu: { gpu_count: 2 } }
    const mode = getInferenceMode(status)
    expect(mode.isLocal).toBe(true)
    expect(isRemoteInference(status)).toBe(false)
  })

  it('defaults to local when mode/source are absent', () => {
    const mode = getInferenceMode({ gpu: { gpu_count: 1 } })
    expect(mode.mode).toBe('local')
    expect(mode.isLocal).toBe(true)
  })

  it('does not treat an unverified configured provider as running', () => {
    // No inferenceMode/inferenceSource means the backend did not prove a
    // remote runtime; the UI must not claim cloud inference.
    const status = { configuredModel: 'some-remote-model', gpu: { gpu_count: 2 } }
    expect(isRemoteInference(status)).toBe(false)
  })
})
