"""Fixed, nonsecret progress from the host's actual activation owner/result."""

PHASES = frozenset({'preparing', 'loading', 'verifying', 'rolling_back', 'rollback_verifying'})
FAILURES = frozenset({'runtime_load_failed', 'runtime_readiness_failed',
                      'consumer_verification_failed', 'rollback_unconfirmed'})
OUTCOMES = frozenset({'activated', 'rolled_back', 'rollback_unconfirmed'})
NEUTRAL_OPERATIONS = frozenset({'pixel_startup_reproof', 'pixel_access_mode', 'pixel_open_app',
                                'pixel_providers', 'pixel_settings'})


def model_activation_status(status):
    if not isinstance(status, dict):
        return None
    # Never let an old outcome replace progress for a currently owned operation.
    operation = status.get('activeOperation')
    if operation == 'model_activation':
        phase, failure = status.get('activationPhase'), status.get('activationFailureCode')
        if (status.get('lifecycleActive') is not True or status.get('activeOperation') != 'model_activation'
                or not isinstance(phase, str) or phase not in PHASES
                or failure is not None and (not isinstance(failure, str) or failure not in FAILURES)):
            return None
        return {'active': True, 'phase': phase, 'failureCode': failure}
    if (status.get('lifecycleActive') or operation) and (
            not isinstance(operation, str) or operation not in NEUTRAL_OPERATIONS):
        return None
    result = status.get('activationResult')
    if not isinstance(result, dict):
        return None
    outcome, failure = result.get('outcome'), result.get('failureCode')
    if (not isinstance(outcome, str) or outcome not in OUTCOMES
            or failure is not None and (not isinstance(failure, str) or failure not in FAILURES)):
        return None
    return {'active': False, 'outcome': outcome, 'failureCode': failure}
