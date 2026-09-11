"""Fixed private checkpoint pipe. No API keys, model calls or child commands."""
import json
import os
from pathlib import Path
import select
import signal
import struct
import sys
import threading
import time

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from pixel_provider.handoff_approvals import FRAME_LIMIT,HandoffApprovals,decode_large
from pixel_provider.store import StoreError


def read_request(stream):
    def exact(size):
        data=bytearray()
        while len(data)<size:
            part=stream.read(size-len(data))
            if not part: raise StoreError('truncated-handoff-frame')
            data.extend(part)
        return bytes(data)
    size=struct.unpack('!I',exact(4))[0]
    if not 0<size<=FRAME_LIMIT: raise StoreError('invalid-handoff-frame')
    value=decode_large(exact(size))
    if (type(value) is not dict or set(value)!={'schemaVersion','checkpointJson','checkpointDigest','timeoutSeconds'}
            or type(value['schemaVersion']) is not int or value['schemaVersion']!=1):
        raise StoreError('invalid-handoff-frame')
    return value


def main():
    if os.name!='posix' or len(sys.argv)!=3 or sys.argv[1]!='--provider-directory' or not os.path.isabs(sys.argv[2]):
        return 2
    import resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    stopped=threading.Event(); done=threading.Event(); armed=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stopped.set())
    signal.signal(signal.SIGINT,lambda *_:stopped.set())
    def watch():
        if not armed.wait(10): os._exit(125)
        while not done.is_set() and not stopped.is_set():
            if select.select([0],[],[],.05)[0]:
                os.read(0,1); stopped.set()
        if stopped.is_set() and not done.wait(2): os._exit(125)
    threading.Thread(target=watch,daemon=True,name='handoff-parent-watch').start()
    try:
        with os.fdopen(os.dup(0),'rb',buffering=0) as stream: value=read_request(stream)
        manager=HandoffApprovals(sys.argv[2])
        publication=manager.publish(value['checkpointJson'],value['checkpointDigest'],value['timeoutSeconds'])
        if select.select([0],[],[],0)[0]: raise StoreError('handoff-parent-disconnected')
        armed.set(); deadline=time.monotonic()+value['timeoutSeconds']
        with publication:
            while not stopped.is_set() and time.monotonic()<deadline:
                receipt=publication.receipt()
                if receipt is not None:
                    if stopped.is_set(): break
                    raw=json.dumps(receipt,separators=(',',':')).encode()
                    sys.stdout.buffer.write(struct.pack('!I',len(raw))+raw); sys.stdout.buffer.flush()
                    publication.finish('approved' if receipt['approved'] else 'declined')
                    return 0
                stopped.wait(.05)
            publication.finish('cancelled' if stopped.is_set() else 'expired')
        return 1
    except Exception:
        sys.stderr.write('handoff-worker-unavailable\n')
        return 1
    finally:
        done.set(); armed.set()


if __name__=='__main__':
    raise SystemExit(main())
