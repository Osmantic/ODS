"""Resume catalog installation without replaying an uncertain host request.

The caller holds the shared installation lock, and supplies the existing
per-service lifecycle lock and trusted installer. No shell or secrets enter
this coordinator. One request advances at most one service.
"""
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

from extension_install_plan import ID


class InstallationJournal:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError('Installation journal is a symlink')
        self.records = {}
        if self.path.exists():
            if self.path.stat().st_size > 1024 * 1024:
                raise ValueError('Installation journal is oversized')
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'records'}
                    or value['schemaVersion'] != 1 or not isinstance(value['records'], dict)):
                raise ValueError('Invalid installation journal')
            for key, record in value['records'].items():
                if (not ID.fullmatch(key) or not isinstance(record, dict)
                        or set(record) not in ({'action', 'state'}, {'action', 'state', 'operationId'},
                            {'action', 'state', 'operationId', 'retryRequestId'})
                        or ('operationId' in record and (not isinstance(record['operationId'], str)
                            or not re.fullmatch(r'[a-f0-9]{32}', record['operationId'])))
                        or ('retryRequestId' in record and (not isinstance(record['retryRequestId'], str)
                            or not re.fullmatch(r'[a-f0-9]{64}', record['retryRequestId'])))
                        or record['action'] not in ('install', 'enable')
                        or record['state'] not in ('dispatching', 'accepted', 'uncertain')):
                    raise ValueError('Invalid installation journal record')
            self.records = value['records']

    def save(self):
        if self.path.is_symlink():
            raise ValueError('Installation journal is a symlink')
        fd, temporary = tempfile.mkstemp(prefix='.install-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'schemaVersion': 1, 'records': self.records}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if os.name == 'posix':
                directory_fd = os.open(self.path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def verify_failed_attempt(journal, service_id, observe):
    """Read the exact terminal attempt before a coordinator revises its recipe.

    A catalog error or absent worker is not enough. No journal mutation happens
    here, and a lost observation must never authorize another installation.
    """
    record = journal.records.get(service_id)
    if (not isinstance(record, dict) or record.get('action') != 'install'
            or not isinstance(record.get('operationId'), str)
            or not re.fullmatch(r'[a-f0-9]{32}', record['operationId'])):
        raise ValueError('No exact installation attempt to reconcile')
    receipt = observe(service_id, record['operationId'])
    if (not isinstance(receipt, dict) or receipt.get('service_id') != service_id
            or receipt.get('operation_id') != record['operationId']
            or receipt.get('state') != 'failed'):
        raise ValueError('Installation failure is not confirmed')
    return dict(record)


def retire_failed_attempt(journal, service_id, expected_record, observe):
    """After durable recipe commit, retire only its unchanged failed attempt.

    Caller retains the receipt and old recipe in the revision journal and holds
    the coordinator/lifecycle locks. This function neither dispatches nor erases
    host receipts. A subsequent advance obtains a new operation identity.
    """
    if verify_failed_attempt(journal, service_id, observe) != expected_record:
        raise ValueError('Installation attempt changed during revision')
    del journal.records[service_id]
    try:
        journal.save()
    except Exception:
        journal.records[service_id] = expected_record
        raise


def advance_installation(read_plan, journal, operation_lock, dispatch, *, observe=None,
                         retry_request_id=None):
    """Recheck after acquiring the lifecycle lock; retain ambiguous effects."""
    if retry_request_id is not None and (not isinstance(retry_request_id, str)
            or not re.fullmatch(r'[a-f0-9]{64}', retry_request_id) or observe is None):
        raise ValueError('Invalid managed retry identity')

    def choose(plan):
        # A healthy observation reconciles a prior request. Never equate the
        # install endpoint's HTTP acceptance with application readiness.
        reconciled = [s['extensionId'] for s in plan['steps']
                      if s['action'] == 'none' and s['extensionId'] in journal.records
                      and not journal.records[s['extensionId']].get('operationId')]
        for key in reconciled:
            del journal.records[key]
        if reconciled:
            journal.save()
        # Query the exact host attempt before interpreting a stale catalog.
        # Missing receipts and transport errors never authorize another POST.
        for step in plan['steps']:
            record = journal.records.get(step['extensionId'], {})
            if record.get('operationId') and observe is not None:
                try:
                    receipt = observe(step['extensionId'], record['operationId'])
                except Exception:
                    receipt = None
                if (not isinstance(receipt, dict)
                        or receipt.get('service_id') != step['extensionId']
                        or receipt.get('operation_id') != record['operationId']):
                    return 'reconciliation_required', step
                state = receipt.get('state')
                if state == 'succeeded' and step['action'] == 'none':
                    del journal.records[step['extensionId']]
                    journal.save()
                    continue
                if state in {'accepted', 'running'}:
                    return 'pending', step
                if state == 'failed':
                    # A retry is a separate, explicit request. It is permitted
                    # only for this target's exact terminal host attempt and
                    # unchanged installed definition. Never replay the same
                    # failed attempt within one owner request.
                    if (retry_request_id and record['action'] == 'install'
                            and record.get('retryRequestId') != retry_request_id
                            and step['extensionId'] == plan['extensionId']
                            and step['status'] == 'error'
                            and all(peer['action'] == 'none' for peer in plan['steps']
                                    if peer['extensionId'] != step['extensionId'])
                            and not any(field['required'] and not field['configured']
                                        for field in step['configuration'])):
                        return 'retry_ready', step
                    return 'failed', step
                # A successful operation still needs catalog/runtime evidence.
                return 'reconciliation_required', step
        if plan['blocked']:
            return 'blocked', None
        if plan['requiresConfiguration']:
            return 'configuration_required', None
        for step in plan['steps']:
            if step['action'] == 'none':
                continue
            if step['action'] == 'wait':
                return 'pending', step
            if step['extensionId'] in journal.records:
                # Includes a crash between sending to the host and saving the
                # reply. Do not turn a missing/stale status into another POST.
                return 'reconciliation_required', step
            if retry_request_id is not None:
                return 'blocked', step
            return 'ready', step
        return 'succeeded', None

    def result(state, plan, step=None, dispatched=False):
        return {'schemaVersion': 1, 'extensionId': plan['extensionId'],
                'state': state, 'activeExtensionId': step['extensionId'] if step else None,
                'operationId': journal.records.get(step['extensionId'], {}).get('operationId') if step else None,
                'dispatched': dispatched, 'plan': plan}

    plan = read_plan()
    state, step = choose(plan)
    if state not in {'ready', 'retry_ready'}:
        return result(state, plan, step)
    selected = step['extensionId']
    with operation_lock(selected):
        # The Extensions page can have changed the same service while this
        # request waited. All mutations use the same per-service lock.
        plan = read_plan()
        state, step = choose(plan)
        if state not in {'ready', 'retry_ready'}:
            return result(state, plan, step)
        if step['extensionId'] != selected:
            return result('pending', plan, step)
        action = 'install' if state == 'retry_ready' else step['action']
        journal.records[selected] = {'action': action, 'state': 'dispatching'}
        if observe is not None and action == 'install':
            journal.records[selected]['operationId'] = secrets.token_hex(16)
        if state == 'retry_ready':
            journal.records[selected]['retryRequestId'] = retry_request_id
        journal.save()  # Must succeed before causing an external effect.
        try:
            if 'operationId' in journal.records[selected]:
                dispatch(selected, action, operation_id=journal.records[selected]['operationId'])
            else:
                dispatch(selected, action)
        except Exception:
            # Do not expose host errors, configuration values, or raw logs.
            journal.records[selected]['state'] = 'uncertain'
            journal.save()
            return result('reconciliation_required', plan, step, dispatched=True)
        journal.records[selected]['state'] = 'accepted'
        journal.save()
        return result('pending', plan, step, dispatched=True)
