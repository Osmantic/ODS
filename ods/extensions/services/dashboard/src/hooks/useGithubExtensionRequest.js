import { useCallback, useEffect, useRef, useState } from 'react'

export function githubExtensionRepository(command) {
  const match = typeof command === 'string' && command.trim().match(/^(?:\/goal\s+)?\/extensions?\s+(https:\/\/github\.com\/[^\s]+)(?:\s|$)/i)
  if (!match) return null
  try {
    const url = new URL(match[1])
    if (url.protocol !== 'https:' || url.host !== 'github.com' || url.search || url.hash || url.username || url.password) return null
    const parts = url.pathname.replace(/\/$/, '').split('/').slice(1)
    if (parts.length !== 2) return null
    const [owner, rawName] = parts
    const name = rawName.replace(/\.git$/, '')
    if (!/^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(owner) || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(name)) return null
    return `https://github.com/${owner}/${name}`.toLowerCase()
  } catch { return null }
}

async function requestScope(body, { signal, fetcher = fetch, status = false } = {}) {
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) abort()
  const timeout = setTimeout(abort, 15000)
  try {
    const response = await fetcher('/api/extensions/github/requests' + (status ? '/status' : ''), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), cache: 'no-store', signal: controller.signal,
      keepalive: body.action === 'cancel',
    })
    if (!response.ok) throw new Error('Extension request unavailable')
    return await response.json()
  } finally { clearTimeout(timeout); signal?.removeEventListener('abort', abort) }
}

function verifyScope(receipt, run, proposal) {
  if (receipt?.schemaVersion !== 1 || receipt.chatId !== run.chatId || receipt.requestId !== run.requestId ||
      receipt.repository !== run.repository || receipt.state !== 'pending' || receipt.installationStarted !== false) {
    throw new Error('Extension request is no longer active')
  }
  const next = receipt.proposal
  if (next && (!/^[a-f0-9]{64}$/.test(next.draftId) || !/^[a-f0-9]{64}$/.test(next.recipeDigest) ||
      !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(next.extensionId))) throw new Error('Invalid extension proposal')
  if (proposal && (!next || ['draftId', 'recipeDigest', 'extensionId'].some(key => next[key] !== proposal[key]))) {
    throw new Error('Extension proposal changed')
  }
  return next
}

const waitForProposal = signal => new Promise(resolve => {
  const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve() }
  const timer = setTimeout(finish, 5000)
  signal.addEventListener('abort', finish, { once: true })
  if (signal.aborted) finish()
})

export async function observeGithubExtension(run, initialReceipt, signal, report, fetcher = fetch) {
  const identity = { chatId: run.chatId, requestId: run.requestId }
  let receipt = initialReceipt
  for (let attempt = 0; attempt < 720 && !signal.aborted; attempt++) {
    const proposal = verifyScope(receipt, run)
    if (!proposal) {
      await waitForProposal(signal)
      if (signal.aborted) return
      receipt = await requestScope({ action: 'read', ...identity }, { signal, fetcher })
      continue
    }
    // This observer never prepares or installs. The agent submits managed
    // operations; opening or polling the interface cannot start one.
    const observed = await requestScope(identity, { signal, fetcher, status: true })
    if (signal.aborted) return
    if (observed?.schemaVersion !== 1 || observed.kind !== 'ods-extension-request-status' ||
        observed.chatId !== run.chatId || observed.requestId !== run.requestId ||
        observed.extensionId !== proposal.extensionId || observed.requestState !== 'pending' ||
        observed.proposalAccepted !== true || typeof observed.prepared !== 'boolean' ||
        !['not_observed','enabled','cli_installed','disabled','stopped','not_installed','installing','setting_up','unhealthy','error','unavailable'].includes(observed.runtimeStatus) ||
        (!observed.prepared && observed.runtimeStatus !== 'not_observed')) {
      throw new Error('Extension observation could not be confirmed')
    }
    const state = ['enabled','cli_installed'].includes(observed.runtimeStatus) ? 'succeeded'
      : observed.runtimeStatus === 'error' ? 'failed'
      : ['unhealthy','unavailable'].includes(observed.runtimeStatus) ? 'reconciliation_required'
      : ['installing','setting_up'].includes(observed.runtimeStatus) ? 'pending' : 'prepared'
    report({ target: observed.prepared ? proposal.extensionId : undefined, state })
    if (['succeeded','failed','reconciliation_required'].includes(state)) return
    await waitForProposal(signal)
    if (signal.aborted) return
    receipt = await requestScope({ action: 'read', ...identity }, { signal, fetcher })
    verifyScope(receipt, run, proposal)

  }
  if (!signal.aborted) throw new Error('Extension observation timed out')
}

export default function useGithubExtensionRequest(chatId) {
  const current = useRef(null)
  const [state, setState] = useState(null)
  const detach = useCallback(() => {
    const run = current.current
    current.current = null
    setState(null)
    if (!run) return null
    run.controller.abort()
    run.signal?.removeEventListener('abort', run.cancel)
    return run
  }, [])
  const stop = useCallback(() => {
    const run = detach()
    if (!run) return
    // Only explicit cancellation changes durable request state. Navigation
    // and component cleanup stop observation, not an authorized operation.
    void requestScope({ action: 'cancel', chatId: run.chatId, requestId: run.requestId }).catch(() => {})
  }, [detach])
  useEffect(() => { detach(); return detach }, [chatId, detach])
  const start = useCallback((command, identity, signal) => {
    if (current.current?.requestId === identity?.requestId && current.current?.chatId === identity?.chatId) return
    const repository = githubExtensionRepository(command)
    if (!repository) {
      // Keep the original request scope for conversational follow-ups. The
      // backend supplies its verified routing identity to the next model turn.
      if (typeof command === 'string' && command.trimStart().startsWith('/')) stop()
      return
    }
    if (signal?.aborted || !identity?.chatId || !identity?.requestId) return
    stop()
    const run = { ...identity, repository, command, signal, cancel: stop, controller: new AbortController() }
    current.current = run
    signal?.addEventListener('abort', run.cancel, { once: true })
    setState({ command, state: 'researching' })
    const report = value => {
      run.target = value.target
      if (current.current === run && !run.controller.signal.aborted) setState({ ...value, command, requestId: run.requestId, chatId: run.chatId })
    }
    const failed = () => {
      if (current.current === run) {
        // A timeout is not cancellation. Keep the original durable scope so
        // readback can recover an accepted proposal or host operation.
        run.busy = false
        setState({ target: run.target, command, requestId: run.requestId, chatId: run.chatId, state: 'reconciliation_required' })
      }
    }
    const execute = async receipt => {
      if (current.current !== run) return
      try { await observeGithubExtension(run, receipt, run.controller.signal, report) }
      finally { run.busy = false }
    }
    run.busy = true
    run.resume = () => {
      if (run.busy || current.current !== run || run.controller.signal.aborted) return
      run.busy = true
      void requestScope({ action: 'read', ...identity }, { signal: run.controller.signal }).then(execute).catch(failed)
    }
    void requestScope({ action: 'create', ...identity, command }).then(execute).catch(failed)
  }, [stop])
  const resume = useCallback(() => current.current?.resume?.(), [])
  return { state, start, stop, resume }
}
