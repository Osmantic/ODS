"""Build an explicit environment boundary for native Pixel LaunchAgents.

launchd EnvironmentVariables adds to the login session environment; it does
not replace it. env -i clears inherited agent sockets, proxy variables and Node
options before the gateway starts. This is not a host isolation boundary.
"""
import copy
import os


ALLOWED = frozenset({
    'HOME', 'PATH', 'TMPDIR', 'DOCKER_HOST', 'DOCKER_CONFIG',
    'OPENCLAW_STATE_DIR', 'OPENCLAW_CONFIG_PATH', 'OPENCLAW_SKIP_CHANNELS',
})
REQUIRED = frozenset({'HOME', 'PATH', 'OPENCLAW_STATE_DIR', 'OPENCLAW_CONFIG_PATH'})


def clean_gateway_document(document):
    """Return a new plist document; inputs must be installer-owned metadata."""
    result = copy.deepcopy(document)
    env = result.get('EnvironmentVariables')
    args = result.get('ProgramArguments')
    if (not isinstance(env, dict) or not REQUIRED <= env.keys()
            or not env.keys() <= ALLOWED
            or any(not isinstance(v, str) or '\0' in v for v in env.values())
            or not isinstance(args, list) or not args
            or any(not isinstance(a, str) or '\0' in a for a in args)
            or not os.path.isabs(args[0]) or 'Program' in result
            or args[0] == '/usr/bin/env'):
        raise ValueError('invalid-native-gateway-environment')
    result['ProgramArguments'] = ['/usr/bin/env', '-i',
                                  *(key + '=' + env[key] for key in sorted(env)),
                                  *args]
    # Do not keep a second, divergent environment specification in the plist.
    result.pop('EnvironmentVariables')
    return result
