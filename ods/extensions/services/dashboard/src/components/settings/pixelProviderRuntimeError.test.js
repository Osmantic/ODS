import { providerRuntimeErrorMessage, readProviderRuntimeError } from './pixelProviderRuntimeError'

const body = reason => JSON.stringify({ detail: { reason, message: 'private-sentinel' } })

it('reads a multi-chunk error and never displays the remote message', async () => {
  const bytes = new TextEncoder().encode(body('provider-inspection-changed'))
  const response = new globalThis.Response(new globalThis.ReadableStream({ start(controller) {
    controller.enqueue(bytes.slice(0, 9))
    controller.enqueue(bytes.slice(9))
    controller.close()
  } }), { status: 409 })
  const reason = await readProviderRuntimeError(response)
  expect(reason).toBe('provider-inspection-changed')
  expect(providerRuntimeErrorMessage(reason)).not.toContain('private-sentinel')
  expect(providerRuntimeErrorMessage('private-sentinel')).toBeNull()
})

it('cancels an oversized stream without consuming subsequent chunks', async () => {
  const cancel = vi.fn()
  const response = new globalThis.Response(new globalThis.ReadableStream({ start(controller) {
    controller.enqueue(new Uint8Array(2049))
  }, cancel }), { status: 409 })
  expect(await readProviderRuntimeError(response)).toBeNull()
  expect(cancel).toHaveBeenCalledOnce()
})

it.each([
  'null', '{}', '[]', '{',
  '{"detail":{"reason":"provider-inspection-changed","message":null}}',
  '{"detail":{"reason":"provider-inspection-changed","message":"x","extra":true}}',
  body('private-sentinel'),
])('rejects an invalid envelope: %s', async value => {
  expect(await readProviderRuntimeError(new globalThis.Response(value, { status: 409 }))).toBeNull()
})

it('rejects malformed UTF-8 and unsupported status codes', async () => {
  expect(await readProviderRuntimeError(new globalThis.Response(new Uint8Array([255]), { status: 409 }))).toBeNull()
  expect(await readProviderRuntimeError(new globalThis.Response(body('provider-inspection-changed'), { status: 401 }))).toBeNull()
})
