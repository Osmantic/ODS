export const canStartReviewedApp = value => !value || ['absent', 'prepared', 'cancelled', 'disconnected', 'failed'].includes(value.phase)

export function authorizationLink(value) {
  if (typeof value !== 'string' || value.length > 7000 || !/^[\x21-\x7e]+$/.test(value)) return null
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && (url.protocol === 'https:' || url.hostname === '127.0.0.1')
      && !url.username && !url.password && !url.hash && url.search ? value : null
  } catch { return null }
}

export function reviewedAppView(value) {
  if (!value || value.phase === 'absent') return {label: 'Not connected', message: null}
  if (value.phase === 'ready' && value.portalAccessVerified === true && value.scope === 'pixel-owner-default') {
    return {label: 'Connected', message: 'Portal can use this app with its current default model.'}
  }
  if (value.phase === 'disconnected') return {label: 'Disconnected',
    message: 'Portal access was removed on this machine. Manage authorization separately in Notion.'}
  if (value.phase === 'cancelled') return {label: 'Sign-in cancelled', message: 'Portal access was not turned on by this attempt.'}
  if (value.phase === 'prepared') return {label: 'Ready to sign in', message: 'Continue to sign in and allow the access shown by Notion.'}
  if (value.phase === 'configured' && value.error === 'native-app-state-changed') return {label: 'Setup needs checking',
    message: 'The saved app setup changed. Disconnect it here before connecting it again.'}
  if (value.phase === 'configured') return {label: 'Access needs checking',
    message: 'This app is already configured. Check access to verify it with Portal’s current default model.'}
  if (value.phase === 'consent') return {label: 'Waiting for sign-in', message: 'Complete the sign-in in the tab opened for this connection.'}
  if (['withdrawing', 'forgetting'].includes(value.phase)) return {label: 'Removing Portal access',
    message: 'The update must finish before ODS can confirm that this app is disconnected.'}
  if (value.error === 'native-app-sign-in-required') return {label: 'Sign-in needs a fix',
    message: 'Disconnect this app here, then sign in again to restore Portal access.'}
  if (value.error === 'native-app-verification-required') return {label: 'Access needs checking',
    message: 'Check access to confirm this app works with Portal’s current default model.'}
  if (value.phase === 'failed') return {label: 'Connection did not finish',
    message: 'ODS preserved the saved state. You can try signing in again, or remove the connection here.'}
  return {label: value.error ? 'Update needs checking' : 'Checking Portal access',
    message: value.error ? 'ODS could not confirm this update. Use Finish before starting another sign-in.'
      : 'Signing in alone does not confirm Portal access. ODS is checking the connection.'}
}
