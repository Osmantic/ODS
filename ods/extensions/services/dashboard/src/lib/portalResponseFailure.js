// Public error codes come from the ingress. Raw provider messages can contain
// credentials or request data and must never be rendered as user-facing errors.
export function portalResponseFailure(error) {
  if (error?.type === 'pixel_ingress_error' && error?.code === 'provider_rate_limited') {
    return 'The model provider reached its rate limit. Wait before continuing, then check the saved work and resume from it.'
  }
  return 'Portal could not complete the response.'
}
