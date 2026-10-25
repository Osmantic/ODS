const STORAGE_KEY = 'ods-talk-conversation-v1'

function fresh() {
  const token = () => Array.from(crypto.getRandomValues(new Uint8Array(32)), b => b.toString(16).padStart(2, '0')).join('')
  return { handle: token(), tab: token(), established: false }
}

// A duplicated tab can inherit sessionStorage. Hold a browser lock for the
// document lifetime so the duplicate receives a fresh conversation identity.
export async function claimConversation() {
  if (!navigator.locks?.request) throw new Error('Durable conversations require a browser with Web Locks support.')
  let identity
  try { identity = JSON.parse(sessionStorage.getItem(STORAGE_KEY)) } catch { /* fresh tab */ }
  if (!/^[a-f0-9]{64}$/.test(identity?.handle) || !/^[a-f0-9]{64}$/.test(identity?.tab)) identity = fresh()
  const acquire = value => new Promise((resolve, reject) => {
    navigator.locks.request(`ods-talk:${value.handle}`, { ifAvailable: true }, lock => {
      if (!lock) { resolve(null); return }
      return new Promise(release => resolve({ identity: value, release }))
    }).catch(reject)
  })
  let claimed = await acquire(identity)
  if (!claimed) claimed = await acquire(fresh())
  if (!claimed) throw new Error('Could not reserve a conversation for this tab.')
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(claimed.identity))
  } catch (error) {
    claimed.release()
    throw error
  }
  return claimed
}

export function conversationHeaders(claimed) {
  if (!claimed) return {}
  return { 'X-ODS-Talk-Tab': claimed.identity.tab, 'X-ODS-Talk-Conversation': claimed.identity.handle }
}

export function establishConversation(claimed) {
  claimed.identity.established = true
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(claimed.identity))
}

export function forgetConversation() {
  sessionStorage.removeItem(STORAGE_KEY)
}
