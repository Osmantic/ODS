"""Authenticated reviewed-app facade over Root's existing managed controller.

No provider settings arrive here. The release fixes the app, registry entry and
read tools, while the controller owns all consent, grant and withdrawal state.
"""
import platform

from pixel_access_bridge import AccessError
from . import native_contract as wire
from .native_enrollment import Controller
from .native_mcp import NativeError
from .native_access import Observation


def control(bridge, operation, request):
    if not wire.valid_action({'operation': operation, 'request': request}):
        raise AccessError('invalid-request')
    try:
        # One deadline across recovery AND a subsequent authorization claim.
        # Sequential controller calls must not reset a public action to 600s.
        budget = 20 if operation == 'connection-app-status' else 70 if operation in ('connection-app-complete', 'connection-app-check') else 300
        with bridge.bounded(budget):
            return wire.public_reply(operation, _dispatch(bridge, operation, request))
    except NativeError as error:
        raise AccessError(error.code) from None
    except ValueError:
        raise AccessError('native-app-result-unconfirmed') from None


def _dispatch(bridge, operation, request):
    if operation == 'connection-app-status':
        surface = 'wsl-systemd' if 'microsoft' in platform.release().lower() else 'linux-systemd'
        return {'schemaVersion': 1, 'available': True, 'surface': surface, 'reason': None,
                'apps': {app: Observation(bridge, _controller(bridge, app)).status() for app in wire.APP_IDS}}
    controller = _controller(bridge, request['app'])
    if operation == 'connection-app-check':
        return Observation(bridge, controller).check()
    if operation == 'connection-app-begin':
        return controller.begin(redirect_port=request['redirectPort'])
    if operation == 'connection-app-complete':
        return controller.complete(flow_id=request['flowId'], callback_url=request['address'])
    if operation == 'connection-app-finish':
        # Recovery never replays an OAuth start or callback. An authorized flow
        # needs a distinct, durable claim; recover() alone creates no grant.
        result = controller.recover(flow_id=request['flowId'])
        result = controller.activate(flow_id=request['flowId']) if result['phase'] == 'authorized' else result
        return Observation(bridge, controller).status() if result['phase'] == 'ready' else result
    if operation == 'connection-app-cancel':
        return controller.cancel(flow_id=request['flowId'])
    return controller.disconnect()


def _controller(bridge, app):
    controller = Controller(bridge, app=app)
    controller._on_access = Observation(bridge, controller).record_enrollment
    return controller
