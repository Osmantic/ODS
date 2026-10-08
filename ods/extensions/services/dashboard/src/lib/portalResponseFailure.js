// Public error codes come from the dashboard or ingress. Provider messages can contain
// credentials or request data and must never be rendered as user-facing errors.
export function isProviderRateLimit(error) {
  return error?.type === 'pixel_ingress_error' && error?.code === 'provider_rate_limited'
}

export function isAdmissionRejected(error) {
  return error?.type === 'pixel_dashboard_error' && error?.code === 'transition_in_progress'
}

const ADMISSION_REJECTION_MESSAGE = 'Portal is holding new messages. This message was not started. Your draft is preserved; check Portal status before sending it again.'

export function portalResponseFailure(error) {
  if (isAdmissionRejected(error)) return ADMISSION_REJECTION_MESSAGE
  if (isProviderRateLimit(error)) {
    return 'The model provider reached its rate limit. Wait before continuing, then check the saved work and resume from it.'
  }
  return 'Portal could not complete the response.'
}
