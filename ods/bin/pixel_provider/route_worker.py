"""One process, one frozen gateway, no tools or child commands.

Only a trusted host launcher may supply the provider directory and reviewed
activation request. This internal pipe is not a public activation endpoint.
Process death closes every listener and OS lock; durable claims prohibit replay.
"""
import os
from pathlib import Path
import resource
import select
import signal
import sys
import threading
import time

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from pixel_provider.advice_frames import encode_frame,read_frame
from pixel_provider.lease_claim import LeaseClaim
from pixel_provider.runtime_session import ProviderSession
from pixel_provider.store import StoreError


def validate_request(value):
    required = {'schemaVersion','runId','sessionId','expectedRevision','allowCloud','timeoutSeconds','confirmed'}
    if (not isinstance(value,dict) or set(value) not in (required,required|{'handoffProviderId'},required|{'scopeSessionKey'})
            or type(value['schemaVersion']) is not int or value['schemaVersion']!=1
            or value['confirmed'] is not True or type(value['allowCloud']) is not bool
            or type(value['timeoutSeconds']) is not int or not 1<=value['timeoutSeconds']<=3600):
        raise StoreError('invalid-provider-lease-request')
    if 'handoffProviderId' in value and (type(value['handoffProviderId']) is not str
            or not value['handoffProviderId'] or len(value['handoffProviderId'])>64):
        raise StoreError('invalid-provider-lease-request')
    return value


def scoped_request(directory, value):
    """Resolve owner state once; the resulting run contract is then frozen."""
    if 'scopeSessionKey' not in value:
        return value
    from pixel_provider.scopes import ScopeStore
    selection = ScopeStore(directory).resolve(value['scopeSessionKey'], value['expectedRevision'])
    if selection is None:
        return value
    # A persisted preference cannot widen the activation's cloud boundary.
    # Both owner authorities must allow transfer; checkpoint approval is separate.
    return dict(value, handoffProviderId=selection['providerId'], allowCloud=value['allowCloud'] and selection['allowCloud'],
                handoffSelectionScope=selection['scope'])


def main():
    if os.name!='posix' or sys.version_info<(3,11):
        return 2
    if len(sys.argv)!=3 or sys.argv[1]!='--provider-directory' or not os.path.isabs(sys.argv[2]):
        return 2
    stopped,done,armed = threading.Event(),threading.Event(),threading.Event()
    duration = [10]
    reason = ['failed']
    phase = 'request'
    def requested_stop(*_args):
        # An orderly owner/supervisor cancellation is not a worker failure.
        # Preserve a deadline/protocol cause already observed by the watchdog.
        if not stopped.is_set():
            reason[0] = 'closed'
            stopped.set()
    def watchdog():
        # Bound even a truncated first frame. No third-party imports, listeners
        # or child processes exist yet. Once armed, this thread owns stdin reads.
        if not armed.wait(10):
            os._exit(125)
        deadline = time.monotonic()+duration[0]
        while not done.is_set() and not stopped.is_set():
            remaining = deadline-time.monotonic()
            if remaining<=0:
                reason[0]='deadline'; stopped.set(); break
            try:
                if select.select([0],[],[],min(.05,remaining))[0]:
                    extra = os.read(0,1)
                    reason[0]='failed' if extra else 'closed'
                    stopped.set()
            except OSError:
                stopped.set()
        if not done.wait(2):
            # A stuck Python/uvicorn thread cannot extend this lease indefinitely.
            # This inference-only process never spawns descendants.
            os._exit(125)
    try:
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        signal.signal(signal.SIGTERM,requested_stop)
        signal.signal(signal.SIGINT,requested_stop)
        threading.Thread(target=watchdog,daemon=True,name='provider-parent-watch').start()
        with os.fdopen(os.dup(0),'rb',buffering=0) as stream:
            value = validate_request(read_frame(stream))
        claim = LeaseClaim(sys.argv[2],value['runId'],value['sessionId'],value['expectedRevision'])
        if select.select([0],[],[],0)[0]:
            raise StoreError('provider-parent-disconnected')
        duration[0]=value['timeoutSeconds']; armed.set()
        phase = 'snapshot'
        value = scoped_request(sys.argv[2], value)
        session = ProviderSession(sys.argv[2],expected_revision=value['expectedRevision'],
            confirmed=value['confirmed'],allow_cloud=value['allowCloud'],
            handoff_provider_id=value.get('handoffProviderId'),handoff_selection_scope=value.get('handoffSelectionScope'))
        phase = 'claim'
        with claim:
            try:
                if stopped.is_set():
                    raise StoreError('provider-lease-cancelled')
                phase = 'serve'
                with session.serve() as lease:
                    if stopped.is_set():
                        raise StoreError('provider-lease-cancelled')
                    sys.stdout.buffer.write(encode_frame(dict(schemaVersion=1,runId=value['runId'],
                        sessionId=value['sessionId'],lease=lease)))
                    sys.stdout.buffer.flush()
                    stopped.wait()
                claim.finish(reason[0],session.events)
            except BaseException:
                try:
                    claim.finish('failed',session.events)
                except (OSError,ValueError):
                    pass
                raise
        return 0
    except Exception:
        sys.stderr.write('provider-lease-worker-failed:'+phase+'\n')
        return 1
    finally:
        done.set(); armed.set()


if __name__=='__main__':
    raise SystemExit(main())
