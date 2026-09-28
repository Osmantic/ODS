// Test-only subset of exclusive ifAvailable Web Locks behavior.
// - Per-name exclusive holders.
// - ifAvailable: true returns null immediately when the name is held.
// - The callback's returned promise retains the lease until it settles.
// - finally removes only the exact holder that acquired the lease.
// - Cleanup runs in a microtask after release so a subsequent request in the
//   same tick observes the name as still held.
// - No forceRelease; callers must release through the returned promise.

export function createFakeLocks() {
  const holders = new Map() // name -> { token, release }

  function request(name, options, callback) {
    if (typeof options === 'function') {
      callback = options
      options = undefined
    }
    const opts = options || {}
    const ifAvailable = opts.ifAvailable === true
    const existing = holders.get(name)
    if (existing) {
      if (ifAvailable) {
        return Promise.resolve(callback(null))
      }
      // Real Web Locks would queue. Tests here only need ifAvailable, so
      // surface a clear error rather than silently blocking.
      return Promise.reject(new Error('fake-locks: queued requests are not supported'))
    }
    const holder = {}
    holders.set(name, holder)
    let result
    try {
      result = callback({ name, mode: opts.mode || 'exclusive' })
    } catch (error) {
      // Native semantics: a throwing callback still releases the lock.
      globalThis.queueMicrotask(() => {
        if (holders.get(name) === holder) holders.delete(name)
      })
      return Promise.reject(error)
    }
    const settle = () => {
      // Only remove the exact holder we installed. A later acquirer that
      // replaced us must not be evicted by our cleanup.
      if (holders.get(name) === holder) holders.delete(name)
    }
    if (result && typeof result.then === 'function') {
      return Promise.resolve(result).then(
        value => { globalThis.queueMicrotask(settle); return value },
        error => { globalThis.queueMicrotask(settle); throw error }
      )
    }
    globalThis.queueMicrotask(settle)
    return Promise.resolve(result)
  }

  return {
    request,
    // Test helpers (not part of the native API).
    _holders: holders,
    _isHeld: name => holders.has(name),
  }
}

export function installFakeLocks() {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis.navigator, 'locks')
  const fake = createFakeLocks()
  Object.defineProperty(globalThis.navigator, 'locks', {
    configurable: true,
    writable: true,
    value: fake,
  })
  return {
    locks: fake,
    restore() {
      if (descriptor) {
        Object.defineProperty(globalThis.navigator, 'locks', descriptor)
      } else {
        delete globalThis.navigator.locks
      }
    },
  }
}

export default createFakeLocks
