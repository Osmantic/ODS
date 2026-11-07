"""Host loopback transport for fixed reviewed apps; Root owns every decision.

Uses the existing listener implementation, with separate app/flow ownership so
a reviewed-app callback cannot replace Google's listener. Codes are never logged
or stored here, and callbacks never claim that Portal access is already ready.
"""
import threading
from typing import Any, Callable

from . import native_apps, native_contract as wire
from .host_api import DISCORD, Relay, _Listener


_listeners: dict[str, _Listener] = {}
_listener_lock = threading.Lock()
_ACTIONS = ('connect', 'complete', 'finish', 'cancel', 'disconnect', 'check')


def _replace_listener(app, listener):
    with _listener_lock:
        previous = _listeners.get(app)
        _listeners[app] = listener
    if previous is not None and previous is not listener:
        previous.close()


def _close_listener(app, flow_id=None):
    with _listener_lock:
        previous = _listeners.get(app)
        if previous is None or flow_id is not None and previous.flow_id != flow_id:
            return
        del _listeners[app]
    previous.close()


def _finish_later(app, flow_id, *, relay, config, log):
    def run():
        try:
            relay('connection-app-finish', {'app': app, 'flowId': flow_id}, config=config)
        except (OSError, ValueError, RuntimeError):
            # No exception body, authorization address or code is a diagnostic.
            # Root retains the exact attempt; Settings offers Finish.
            log('reviewed apps: Portal access still needs checking in Settings')
    threading.Thread(target=run, daemon=True).start()


def status(*, config: dict, relay: Relay):
    code, reply = relay('connection-app-status', {}, config=config)
    if code == 200:
        reply = dict(wire.public_reply('connection-app-status', reply), catalog=native_apps.public_catalog())
    return code, reply


def _complete(app, flow_id, address, *, config, relay, log):
    code, reply = relay('connection-app-complete', {'app': app, 'flowId': flow_id, 'address': address}, config=config)
    if code == 200:
        wire.public_reply('connection-app-complete', reply)
        if reply['phase'] == 'authorized':
            _finish_later(app, flow_id, relay=relay, config=config, log=log)
    return code, reply


def _redirect_page(app, flow_id, port, *, config, relay, log):
    def handle(query):
        # The listener's own bound endpoint, never an HTTP Host header. Root
        # compares this complete URI and state to its saved consent intent.
        address = f'http://127.0.0.1:{port}/?{query}'
        if not wire.valid_callback(address):
            return 'This sign-in address is invalid', 'Return to ODS and check the sign-in tab.', None, False
        try:
            code, reply = _complete(app, flow_id, address, config=config, relay=relay, log=log)
        except (OSError, ValueError, RuntimeError):
            return 'ODS could not confirm this sign-in', 'Return to Connected apps and press Finish before starting another sign-in.', DISCORD, True
        if code == 200 and reply['phase'] == 'authorized':
            return 'Sign-in accepted', 'Return to ODS while it checks whether Portal can use this app.', None, True
        if code == 200:
            return 'Sign-in did not finish', 'Return to Connected apps to see what happened and the next step.', DISCORD, True
        if reply.get('error') in ('state-mismatch', 'sign-in-state-mismatch', 'native-callback-redirect-mismatch', 'invalid-callback'):
            return 'This address belongs to another sign-in', 'Use the tab opened for this ODS connection.', None, False
        return 'This connection needs checking', 'Return to Connected apps and press Finish before trying another sign-in.', DISCORD, True
    return handle


def _begin(app, *, config, relay, log):
    listener = _Listener()
    serving = False
    try:
        code, reply = relay('connection-app-begin', {'app': app, 'redirectPort': listener.port}, config=config)
        if code != 200:
            return code, reply
        wire.public_reply('connection-app-begin', reply)
        if reply['phase'] == 'consent':
            _replace_listener(app, listener)
            listener.serve(reply['flowId'], _redirect_page(app, reply['flowId'], listener.port,
                config=config, relay=relay, log=log))
            serving = True
        elif reply['phase'] == 'authorized':
            # Native OAuth can reuse an existing sign-in. That is not a claim
            # that the owner selected a new account, nor a Portal access proof.
            _finish_later(app, reply['flowId'], relay=relay, config=config, log=log)
        return code, reply
    finally:
        if not serving:
            listener.close()


def action(app: str, name: str, body: Any, *, config: dict, relay: Relay,
           log: Callable[[str], None] = print):
    if app not in wire.APP_IDS or name not in _ACTIONS or type(body) is not dict:
        return 400, {'error': 'invalid-request'}
    if name == 'connect':
        if body != {}:
            return 400, {'error': 'invalid-request'}
        return _begin(app, config=config, relay=relay, log=log)
    operation = 'connection-app-' + name
    payload = dict(body, app=app)
    if 'app' in body or not wire.valid_action({'operation': operation, 'request': payload}):
        return 400, {'error': 'invalid-request'}
    if name == 'complete':
        code, reply = _complete(app, body['flowId'], body['address'], config=config, relay=relay, log=log)
        if code == 200:
            _close_listener(app, body['flowId'])
        return code, reply
    if name in ('cancel', 'disconnect'):
        _close_listener(app, body.get('flowId'))
    code, reply = relay(operation, payload, config=config)
    if code == 200:
        wire.public_reply(operation, reply)
    return code, reply
