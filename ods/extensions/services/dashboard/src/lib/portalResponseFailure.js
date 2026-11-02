// Public error codes come from the dashboard or ingress. Provider messages can contain
// credentials or request data and must never be rendered as user-facing errors.
export function isProviderRateLimit(error) {
  return error?.type === 'pixel_ingress_error' && error?.code === 'provider_rate_limited'
}

// Portal refused the turn before starting it because another picture holds
// its one image slot. Nothing ran, so the owner can simply send it again.
export function isBusyRefusal(error) {
  return error?.type === 'pixel_dashboard_error' && error?.code === 'image_turn_busy'
}

export function isAdmissionRejected(error) {
  return error?.type === 'pixel_dashboard_error' && ['transition_in_progress', 'gateway_connect_refused', 'image_turn_busy'].includes(error?.code)
}

const ADMISSION_REJECTION_MESSAGE = 'Portal is holding new messages. This message was not started. Your draft is preserved; check Portal status before sending it again.'

// A busy refusal is not a failure: no cause to hunt for, nothing to repair.
export function busyRefusalText(withImages) {
  return withImages
    ? 'Portal is still working on another picture, so it did not start this message. Your message and pictures are kept; send them again in a moment.'
    : 'Portal is busy with another request, so it did not start this message. Your message is kept; send it again in a moment.'
}

export function portalResponseFailure(error) {
  if (isBusyRefusal(error)) return busyRefusalText(true)
  if (isAdmissionRejected(error)) {
    return error.code === 'gateway_connect_refused'
      ? 'Portal did not start this attempt because its gateway was unavailable. Your draft is preserved; send it again when Portal is ready.'
      : ADMISSION_REJECTION_MESSAGE
  }
  if (isProviderRateLimit(error)) {
    return 'The model provider reached its rate limit. Wait before continuing, then check the saved work and resume from it.'
  }
  return 'Portal could not complete the response.'
}
