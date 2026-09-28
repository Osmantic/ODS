const NAMESPACE = 'ods:pixel-chat-writer:';
const CHAT_ID_RE = /^[A-Za-z0-9_-]{1,128}$/;

export function createConversationOwnership(locks = globalThis.navigator?.locks) {
  let generation = 0;
  let current = null; // { chatId, admission, release, releaseFn, held, settled }
  let disposed = false;

  function owns(chatId) {
    return !!current && current.chatId === chatId && current.held;
  }

  function settle(entry, result) {
    if (entry.settled) return;
    entry.settled = true;
    entry.resolveAdmission(result);
  }

  function releaseEntry(entry) {
    if (!entry) return;
    if (entry.releaseFn) {
      const fn = entry.releaseFn;
      entry.releaseFn = null;
      try { fn(); } catch { /* ignore */ }
    }
  }

  function acquire(chatId) {
    if (disposed) return Promise.resolve({ owned: false, reason: 'disposed' });
    if (typeof chatId !== 'string' || !CHAT_ID_RE.test(chatId)) {
      return Promise.resolve({ owned: false, reason: 'invalid-chat-id' });
    }
    if (current && current.chatId === chatId) {
      return current.admission;
    }
    if (!locks || typeof locks.request !== 'function') {
      return Promise.resolve({ owned: false, reason: 'unavailable' });
    }

    // Release previous entry BEFORE requesting new one.
    if (current) {
      const prev = current;
      current = null;
      generation++;
      settle(prev, { owned: false, reason: 'released' });
      releaseEntry(prev);
    }

    const myGen = ++generation;
    let resolveAdmission;
    const admission = new Promise((res) => { resolveAdmission = res; });
    let releaseFn;
    const release = new Promise((res) => { releaseFn = res; });
    const entry = {
      chatId,
      admission,
      release,
      releaseFn,
      held: false,
      settled: false,
      resolveAdmission,
    };
    current = entry;

    const isStale = () => disposed || generation !== myGen || current !== entry;

    let requestResult;
    try {
      requestResult = locks.request(
        NAMESPACE + chatId,
        { mode: 'exclusive', ifAvailable: true },
        (lock) => {
          if (!lock) {
            if (!isStale()) {
              current = null;
              settle(entry, { owned: false, reason: 'locked' });
            } else {
              settle(entry, { owned: false, reason: 'released' });
            }
            return undefined;
          }
          if (isStale()) {
            settle(entry, { owned: false, reason: 'released' });
            return undefined;
          }
          entry.held = true;
          settle(entry, { owned: true });
          return release;
        }
      );
    } catch {
      if (!isStale()) {
        current = null;
        entry.held = false;
        releaseEntry(entry);
        settle(entry, { owned: false, reason: 'unavailable' });
      } else {
        settle(entry, { owned: false, reason: 'released' });
      }
      return admission;
    }

    if (requestResult && typeof requestResult.then === 'function') {
      requestResult.then(
        () => {
          if (!isStale() && !entry.held) {
            current = null;
            settle(entry, { owned: false, reason: 'unavailable' });
          } else if (isStale() && !entry.held) {
            settle(entry, { owned: false, reason: 'released' });
          }
        },
        () => {
          if (!isStale()) {
            // Clear failed pending requests as well as granted leases.
            current = null;
            entry.held = false;
            releaseEntry(entry);
            settle(entry, { owned: false, reason: 'unavailable' });
          } else {
            if (entry.held) {
              entry.held = false;
              releaseEntry(entry);
            }
            settle(entry, { owned: false, reason: 'released' });
          }
        }
      );
    }

    return admission;
  }

  function release() {
    if (!current) return;
    const entry = current;
    current = null;
    generation++;
    settle(entry, { owned: false, reason: 'released' });
    releaseEntry(entry);
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    if (current) {
      const entry = current;
      current = null;
      generation++;
      settle(entry, { owned: false, reason: 'disposed' });
      releaseEntry(entry);
    }
  }

  return { owns, acquire, release, dispose };
}

export default createConversationOwnership;
