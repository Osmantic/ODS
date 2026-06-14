import { fireEvent, screen } from '@testing-library/react'
import { render } from '../../test/test-utils'
import { LemonadeProviderContract } from '../LemonadeProviderContract' // eslint-disable-line no-unused-vars


describe('LemonadeProviderContract', () => {
  test('renders provider readiness and actionable capability failures', () => {
    render(
      <LemonadeProviderContract
        runtime={{
          providerStatus: 'blocked',
          loadedModel: 'Qwen3-0.6B-GGUF',
          loadedModels: [
            { modelName: 'Qwen3-0.6B-GGUF', type: 'llm', device: 'gpu' },
            { modelName: 'nomic-embed-text', type: 'embedding', device: 'gpu' },
          ],
          runtimeMode: 'external-lemonade',
          providerCapabilities: [
            { name: 'health', status: 'ok', required: true, detail: '10.7.0' },
            {
              name: 'gateway_chat',
              status: 'failed',
              required: true,
              detail: 'auth_rejected',
              recoveryHint: 'Set LITELLM_KEY to the LiteLLM master key.',
            },
          ],
        }}
      />,
    )

    expect(screen.getByRole('region', { name: /lemonade provider contract/i })).toBeInTheDocument()
    expect(screen.getByText('blocked')).toBeInTheDocument()
    expect(screen.getByText(/2 loaded models/)).toBeInTheDocument()
    expect(screen.getByText('gateway chat')).toBeInTheDocument()
    expect(screen.getByText(/Set LITELLM_KEY/)).toBeInTheDocument()
  })

  test('does not render without provider capabilities', () => {
    const { container } = render(<LemonadeProviderContract runtime={{ providerStatus: 'ready' }} />)

    expect(container).toBeEmptyDOMElement()
  })

  test('offers an explicit active probe for unverified routes', () => {
    const onRunActiveProbe = vi.fn()
    render(
      <LemonadeProviderContract
        runtime={{
          providerStatus: 'unverified',
          providerProbeMode: 'passive',
          providerCapabilities: [
            { name: 'gateway_chat', status: 'unverified', required: true, detail: 'active_probe_required' },
          ],
        }}
        onRunActiveProbe={onRunActiveProbe}
      />,
    )

    expect(screen.getByText('passive probe')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /run active lemonade capability probe/i }))
    expect(onRunActiveProbe).toHaveBeenCalledOnce()
  })
})
