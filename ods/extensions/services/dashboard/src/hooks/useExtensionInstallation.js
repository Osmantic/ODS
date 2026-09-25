import { useCallback, useEffect, useRef, useState } from 'react'
import { extensionSetupTarget } from '../components/PortalExtensionSetup'

const terminal = new Set(['succeeded', 'failed', 'blocked', 'configuration_required', 'reconciliation_required'])

export async function advanceCatalogInstallation(target, signal, report, fetcher = fetch) {
  if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(target)) throw new Error('Invalid extension')
  // Only the server coordinator chooses dependencies and submits host effects.
  // Its durable journal prevents replay after a lost or delayed acknowledgement.
  for (let attempt = 0; attempt < 720 && !signal.aborted; attempt++) {
    const request = new AbortController()
    const abort = () => request.abort()
    signal.addEventListener('abort', abort, { once: true })
    const timeout = setTimeout(abort, 240000)
    let receipt
    try {
      const response = await fetcher(`/api/extensions/${target}/install-next`, {
        method: 'POST', signal: request.signal, cache: 'no-store',
      })
      if (!response.ok) throw new Error('Installation acknowledgement unavailable')
      receipt = await response.json()
      if (receipt?.schemaVersion !== 1 || receipt.extensionId !== target ||
          !['pending', ...terminal].includes(receipt.state) || typeof receipt.dispatched !== 'boolean' ||
          receipt.plan?.extensionId !== target || !Array.isArray(receipt.plan.steps) ||
          !receipt.plan.steps.length || receipt.plan.steps.length > 128 ||
          (receipt.state === 'succeeded' && (receipt.dispatched || receipt.plan.steps.some(step =>
            step.action !== 'none' || !['enabled', 'cli_installed'].includes(step.status))))) {
        throw new Error('Installation receipt is inconsistent')
      }
    } finally {
      clearTimeout(timeout)
      signal.removeEventListener('abort', abort)
    }
    if (signal.aborted) return
    report({ target, state: receipt.state })
    if (terminal.has(receipt.state)) return
    await new Promise(resolve => {
      const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve() }
      const timer = setTimeout(finish, 5000)
      signal.addEventListener('abort', finish, { once: true })
      if (signal.aborted) finish()
    })
  }
  if (!signal.aborted) report({ target, state: 'reconciliation_required' })
}

export default function useExtensionInstallation(chatId) {
  const current = useRef(null)
  const [state, setState] = useState(null)
  const stop = useCallback(() => {
    current.current?.controller.abort()
    current.current = null
    setState(null)
  }, [])
  useEffect(() => { stop(); return () => current.current?.controller.abort() }, [chatId, stop])
  const start = useCallback((command, parentSignal, identity = {}) => {
    const target = extensionSetupTarget(command)
    if (!target || parentSignal?.aborted) return
    if (current.current?.target === target && current.current.command === command && !current.current.controller.signal.aborted) return
    current.current?.controller.abort()
    const controller = new AbortController()
    const run = { target, command, controller }
    current.current = run
    const abort = () => {
      controller.abort()
      if (current.current === run) setState({ target, command, state: 'reconciliation_required' })
    }
    parentSignal?.addEventListener('abort', abort, { once: true })
    const report = value => { if (current.current === run && !controller.signal.aborted) setState({ ...value, command, requestId: identity.requestId, chatId: identity.chatId }) }
    report({ target, state: 'pending' })
    advanceCatalogInstallation(target, controller.signal, report).catch(() => {
      report({ target, state: 'reconciliation_required' })
    }).finally(() => {
      parentSignal?.removeEventListener('abort', abort)
      if (current.current === run) current.current = null
    })
  }, [])
  const resume = useCallback(() => {
    if (state?.chatId !== chatId || state.state !== 'reconciliation_required') return
    start(state.command, undefined, { chatId: state.chatId, requestId: state.requestId })
  }, [chatId, state, start])
  return { state, start, stop, resume }
}
