"""Private checkpoint custody; only the owner API may write a decision.

Publication is an internal runtime operation, not a public HTTP action. A live
worker holds running.lock; process loss can never turn an old approval into a
new run. Same-UID malicious processes are outside this POSIX custody boundary.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time

from .client import _write_private,_json
from .config import _check_id,_check_nonempty_str,_validate_base_url
from .lease_claim import RUN_ID,sync_directory
from .store import ProviderStore,StoreError,decode_document,_pairs,_float,_constant,_check_depth

CHECKPOINT_LIMIT = 2*1024*1024
FRAME_LIMIT = 5*1024*1024


def decode_large(raw,limit=FRAME_LIMIT):
    if type(raw) is not bytes or not raw or len(raw)>limit:
        raise StoreError('invalid-handoff-document')
    try:
        text=raw.decode('utf-8'); _check_depth(text)
        return json.loads(text,object_pairs_hook=_pairs,parse_float=_float,parse_constant=_constant)
    except (ValueError,RecursionError):
        raise StoreError('invalid-handoff-document') from None


def run_id(value):
    if type(value) is not str or not RUN_ID.fullmatch(value):
        raise StoreError('invalid-handoff-run')
    return value.lower()


def validate_checkpoint(raw,digest):
    if (type(digest) is not str or len(digest)!=64
            or hashlib.sha256(raw).hexdigest()!=digest):
        raise StoreError('handoff-checkpoint-mismatch')
    value=decode_large(raw,CHECKPOINT_LIMIT)
    fields={'schemaVersion','runId','sessionId','agentId','workspaceDir','recipient',
            'dataScope','returnAction','prompt','systemPrompt','messages'}
    if (type(value) is not dict or set(value)!=fields or type(value['schemaVersion']) is not int
            or value['schemaVersion']!=1 or value['agentId']!='pixel'
            or value['dataScope']!='conversation-and-this-run-tool-results'
            or value['returnAction']!=('owner-scope-return-or-end' if type(value['recipient']) is dict and 'selectionScope' in value['recipient'] else 'configured-leader-on-next-run')
            or type(value['messages']) is not list):
        raise StoreError('invalid-handoff-checkpoint')
    run_id(value['runId'])
    for key in ('sessionId','workspaceDir','prompt','systemPrompt'):
        if type(value[key]) is not str or '\0' in value[key]:
            raise StoreError('invalid-handoff-checkpoint')
    if not value['sessionId'] or len(value['sessionId'])>256 or not value['workspaceDir']:
        raise StoreError('invalid-handoff-checkpoint')
    pending=set()
    for message in value['messages']:
        if type(message) is not dict: raise StoreError('invalid-handoff-tool-history')
        if message.get('role')=='assistant' and type(message.get('content')) is list:
            for part in message['content']:
                if type(part) is dict and part.get('type')=='toolCall':
                    identity=part.get('id')
                    if type(identity) is not str or not identity or identity in pending:
                        raise StoreError('invalid-handoff-tool-history')
                    pending.add(identity)
        elif message.get('role')=='toolResult':
            identity=message.get('toolCallId')
            if type(identity) is not str or identity not in pending:
                raise StoreError('invalid-handoff-tool-history')
            pending.remove(identity)
    if pending: raise StoreError('invalid-handoff-tool-history')
    validate_recipient(value['recipient'])
    return value


def validate_recipient(recipient):
    fields={'id','label','kind','baseUrl','model','revision','scope','previousProviderId'}
    if (type(recipient) is not dict or set(recipient) not in (fields,fields|{'selectionScope'})
            or 'selectionScope' in recipient and recipient['selectionScope'] not in ('task','conversation','default')
            or recipient['scope']!='run'
            or recipient['kind'] not in ('local','ods-peer','cloud')
            or type(recipient['revision']) is not int or not 0<=recipient['revision']<2**53
            or recipient['id']==recipient['previousProviderId']):
        raise StoreError('invalid-handoff-recipient')
    try:
        for key in ('id','previousProviderId'): _check_id(recipient[key],key)
        for key in ('label','model'): _check_nonempty_str(recipient[key],key)
        if _validate_base_url(recipient['baseUrl'],'endpoint')!=recipient['baseUrl']:
            raise ValueError()
    except ValueError:
        raise StoreError('invalid-handoff-recipient') from None
    return recipient


def read_at(path,directory_fd,name,limit):
    fd=ProviderStore(path)._open_file(directory_fd,name)
    try:
        if os.fstat(fd).st_size>limit: raise StoreError('handoff-document-too-large')
        raw=bytearray()
        while len(raw)<=limit:
            part=os.read(fd,min(65536,limit+1-len(raw)))
            if not part: break
            raw.extend(part)
        if len(raw)>limit: raise StoreError('handoff-document-too-large')
        return bytes(raw)
    finally: os.close(fd)


class HandoffApprovals:
    def __init__(self,directory,*,clock=time.time):
        self.providers=Path(directory).absolute()
        self.root=self.providers/'handoff-approvals'
        self.clock=clock

    def _path(self,identity):
        return self.root/run_id(identity)

    def _read_claim(self,path,fd):
        claim=decode_document(read_at(path,fd,'claim.json',8192))
        if (type(claim) is not dict or set(claim)!={'runId','checkpointDigest','expiresAt','checkpointBytes','recipient'}
                or run_id(claim['runId'])!=path.name or type(claim['expiresAt']) is not int
                or not 0<claim['expiresAt']<2**53
                or type(claim['checkpointDigest']) is not str or not re.fullmatch(r'[a-f0-9]{64}',claim['checkpointDigest'])
                or type(claim['checkpointBytes']) is not int or not 0<claim['checkpointBytes']<=CHECKPOINT_LIMIT):
            raise StoreError('invalid-handoff-claim')
        validate_recipient(claim['recipient'])
        return claim

    def _read(self,path,fd):
        claim=self._read_claim(path,fd)
        raw=read_at(path,fd,'checkpoint.json',CHECKPOINT_LIMIT)
        checkpoint=validate_checkpoint(raw,claim['checkpointDigest'])
        if len(raw)!=claim['checkpointBytes'] or checkpoint['recipient']!=claim['recipient'] or checkpoint['runId']!=claim['runId']:
            raise StoreError('invalid-handoff-claim')
        return claim,raw

    @staticmethod
    def _optional(path,fd,name):
        try: return decode_document(read_at(path,fd,name,8192))
        except FileNotFoundError: return None

    @staticmethod
    def _alive(path,fd):
        import fcntl
        lock=ProviderStore(path)._open_file(fd,'running.lock')
        try:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); return False
            except BlockingIOError: return True
        finally: os.close(lock)

    def _state(self,path,fd,claim):
        final=self._optional(path,fd,'result.json')
        if final is not None:
            if type(final) is not dict or set(final)!={'status'} or final['status'] not in ('approved','declined','expired','cancelled','interrupted'):
                raise StoreError('invalid-handoff-result')
            if final['status'] in ('approved','declined'):
                decision=self._optional(path,fd,'decision.json')
                self._validate_decision(decision,claim)
                if decision['approved']!=(final['status']=='approved'):
                    raise StoreError('invalid-handoff-result')
            return final['status']
        if not self._alive(path,fd): return 'interrupted'
        if self.clock()>=claim['expiresAt']: return 'expired'
        decision=self._optional(path,fd,'decision.json')
        if decision is not None:
            self._validate_decision(decision,claim)
            return 'approved' if decision['approved'] else 'declined'
        return 'pending'

    def status(self,identity,*,checkpoint=False):
        path=self._path(identity)
        with ProviderStore(self.providers)._locked(False),ProviderStore(self.root)._locked(False):
            with ProviderStore(path)._locked(False) as fd:
                claim=self._read_claim(path,fd)
                state=self._state(path,fd,claim)
                # Polling retained terminal claims must not load every transcript.
                # Pending metadata and every explicit preview still verify custody.
                if checkpoint or state=='pending': claim,raw=self._read(path,fd)
                result={**claim,'status':state}
                if checkpoint: result['checkpointJson']=raw.decode('utf-8')
                return result

    def pending(self):
        if not self.providers.exists() and not self.providers.is_symlink():
            return {'items':[],'unavailableCount':0}
        with ProviderStore(self.providers)._locked(False):
            if not self.root.exists() and not self.root.is_symlink():
                return {'items':[],'unavailableCount':0}
            with ProviderStore(self.root)._locked(False):
                names=[]
                with os.scandir(self.root) as entries:
                    for entry in entries:
                        if RUN_ID.fullmatch(entry.name): names.append(entry.name)
                        if len(names)>4096: raise StoreError('handoff-history-limit')
        items=[]; unavailable=0
        for name in names:
            try: result=self.status(name)
            except (StoreError,OSError): unavailable+=1; continue
            if result['status']=='pending': items.append(result)
        return {'items':sorted(items,key=lambda row:row['expiresAt']),'unavailableCount':unavailable}

    @staticmethod
    def _validate_decision(body,claim):
        if (type(body) is not dict or set(body)!={'runId','checkpointDigest','approved','allowCloud','acceptUnknownCost'}
                or body['runId']!=claim['runId'] or body['checkpointDigest']!=claim['checkpointDigest']
                or any(type(body[key]) is not bool for key in ('approved','allowCloud','acceptUnknownCost'))
                or body['approved'] and claim['recipient']['kind']=='cloud' and not (body['allowCloud'] and body['acceptUnknownCost'])):
            raise StoreError('invalid-handoff-decision')

    def decide(self,body):
        if type(body) is not dict: raise StoreError('invalid-handoff-decision')
        path=self._path(body.get('runId'))
        with ProviderStore(self.providers)._locked(False),ProviderStore(self.root)._locked(False):
            with ProviderStore(path)._locked(True) as fd:
                claim,_=self._read(path,fd); self._validate_decision(body,claim)
                previous=self._optional(path,fd,'decision.json')
                if previous is not None:
                    if previous!=body: raise StoreError('handoff-decision-conflict')
                else:
                    if self._state(path,fd,claim)!='pending': raise StoreError('handoff-no-longer-pending')
                    _write_private(path/'decision.json',_json(body)); os.fsync(fd)
        return self.status(body['runId'],checkpoint=True)

    def publish(self,checkpoint_json,digest,timeout_seconds):
        return PendingHandoff(self,checkpoint_json,digest,timeout_seconds)


class PendingHandoff:
    def __init__(self,manager,checkpoint_json,digest,timeout_seconds):
        if type(checkpoint_json) is not str or type(timeout_seconds) is not int or not 1<=timeout_seconds<=120:
            raise StoreError('invalid-handoff-publication')
        self.raw=checkpoint_json.encode('utf-8')
        checkpoint=validate_checkpoint(self.raw,digest)
        self.manager=manager; self.path=manager._path(checkpoint['runId']); self.lock=None
        self.claim={'runId':checkpoint['runId'],'checkpointDigest':digest,'checkpointBytes':len(self.raw),
                    'recipient':checkpoint['recipient'],'expiresAt':int(manager.clock())+timeout_seconds}

    def __enter__(self):
        import fcntl
        manager=self.manager
        try:
            with ProviderStore(manager.providers)._locked(False):
                try: manager.root.mkdir(mode=0o700)
                except FileExistsError: pass
                sync_directory(manager.providers)
                with ProviderStore(manager.root)._locked(True) as root_fd:
                    if self.path.exists() or self.path.is_symlink(): raise StoreError('handoff-run-replayed')
                    if sum(1 for item in manager.root.iterdir() if RUN_ID.fullmatch(item.name))>=4096:
                        raise StoreError('handoff-history-limit')
                    self.path.mkdir(mode=0o700); os.fsync(root_fd)
                    with ProviderStore(self.path)._locked(True) as fd:
                        self.lock=ProviderStore(self.path)._open_file(fd,'running.lock',create=True)
                        fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        _write_private(self.path/'checkpoint.json',self.raw)
                        _write_private(self.path/'claim.json',_json(self.claim)); os.fsync(fd)
            return self
        except BaseException:
            if self.lock is not None: os.close(self.lock); self.lock=None
            raise

    def receipt(self):
        if self.lock is None: raise StoreError('handoff-no-longer-pending')
        with ProviderStore(self.path)._locked(False) as fd:
            claim=self.manager._read_claim(self.path,fd)
            if self.manager._state(self.path,fd,claim) not in ('approved','declined'): return None
            claim,_=self.manager._read(self.path,fd)
            if claim!=self.claim: raise StoreError('handoff-checkpoint-mismatch')
            decision=self.manager._optional(self.path,fd,'decision.json')
            if decision is None: return None
            self.manager._validate_decision(decision,claim)
            receipt={key:decision[key] for key in ('approved','checkpointDigest')}
            if claim['recipient']['kind']=='cloud':
                receipt.update(allowCloud=decision['allowCloud'],acceptUnknownCost=decision['acceptUnknownCost'])
            return receipt

    def finish(self,status):
        if status not in ('approved','declined','expired','cancelled','interrupted') or self.lock is None:
            raise StoreError('invalid-handoff-result')
        with ProviderStore(self.path)._locked(True) as fd:
            claim,_=self.manager._read(self.path,fd)
            if claim!=self.claim: raise StoreError('handoff-checkpoint-mismatch')
            if status in ('approved','declined'):
                decision=self.manager._optional(self.path,fd,'decision.json')
                self.manager._validate_decision(decision,claim)
                if decision['approved']!=(status=='approved'): raise StoreError('invalid-handoff-result')
            _write_private(self.path/'result.json',_json({'status':status})); os.fsync(fd)

    def __exit__(self,*_args):
        if self.lock is not None: os.close(self.lock); self.lock=None


def get_manager(data_dir):
    return HandoffApprovals(Path(data_dir)/'pixel-providers')
