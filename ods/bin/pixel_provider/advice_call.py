"""Host-side snapshot and admission; optional dependencies stay in the worker."""
from .advice import AdvisoryCall
from .advice_process import run_worker
from .advice_runtime import RuntimeStore,resolve_runtime
from .store import StoreError


class WorkerAdvisoryCall(AdvisoryCall):
    def __init__(self,directory,body):
        self.directory = directory
        self.receipt = RuntimeStore(directory).load()
        resolve_runtime(directory,receipt=self.receipt)
        super().__init__(directory,body)

    def run(self,*,cancelled,lock_fds):
        # Re-verify the frozen runtime bytes, not a newly selected pointer. An
        # explicit reprepare never silently changes an already accepted call.
        command = resolve_runtime(self.directory,receipt=self.receipt)
        answer = run_worker(command,dict(schemaVersion=1,requestId=self.body['requestId'],snapshot=self.snapshot()),
                            cancelled=cancelled,deadline_seconds=self.config['policy']['deadlineSeconds'],lock_fds=lock_fds)
        if (set(answer) != {'schemaVersion','requestId','result'} or type(answer['schemaVersion']) is not int
                or answer['schemaVersion'] != 1 or answer['requestId'] != self.body['requestId']):
            raise StoreError('invalid-advice-response')
        result = answer['result']
        if (not isinstance(result,dict) or set(result) != {'text','usage','costStatus','trusted'}
                or result['trusted'] is not False or result['costStatus'] != 'unknown'
                or not isinstance(result['text'],str) or not result['text'].strip()
                or len(result['text'].encode()) > 65536 or not isinstance(result['usage'],dict)
                or set(result['usage']) != {'prompt_tokens','completion_tokens','total_tokens'}
                or any(v is not None and (type(v) is not int or not 0 <= v <= 10**10) for v in result['usage'].values())):
            raise StoreError('invalid-advice-response')
        return result
