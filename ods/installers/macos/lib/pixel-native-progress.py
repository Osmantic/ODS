"""Read and display the retained model worker's progress without restarting it."""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
STATES = frozenset(('starting', 'downloading', 'verifying', 'swapping', 'failed', 'complete'))
LABELS = {'starting': 'Preparing the full model', 'downloading': 'Downloading the full model',
          'verifying': 'Verifying the download', 'swapping': 'Activating the full model',
          'failed': 'Model upgrade stopped', 'complete': 'Model worker finished'}
ERRORS = frozenset(('model-progress-too-large', 'invalid-model-progress',
    'unsafe-model-progress',
    'invalid-model-progress-state', 'invalid-model-progress-number',
    'model-worker-observation-failed', 'duplicate-dashboard-port', 'invalid-dashboard-port'))


def read_record(path):
    """Read bounded worker metadata, never environment contents or raw logs."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('unsafe-model-progress')
        if info.st_size > 65536:
            raise ValueError('model-progress-too-large')
        body = stream.read(65537)
    if len(body) > 65536:
        raise ValueError('model-progress-too-large')
    value = json.loads(body)
    if type(value) is not dict:
        raise ValueError('invalid-model-progress')
    return value


def progress(install_dir):
    record = read_record(Path(install_dir) / 'data/bootstrap-status.json')
    if record is None:
        return {'status': 'not-recorded'}
    state = record.get('status')
    if type(state) is not str or state not in STATES:
        raise ValueError('invalid-model-progress-state')
    result = {'status': state}
    for key in ('percent', 'bytesDownloaded', 'bytesTotal', 'speedBytesPerSec'):
        value = record.get(key)
        if value is not None and (type(value) not in (int, float) or value < 0
                                 or value > 1e30 or not math.isfinite(value)):
            raise ValueError('invalid-model-progress-number')
        result[key] = value
    if result['percent'] is not None and result['percent'] > 100:
        raise ValueError('invalid-model-progress-number')
    updated = record.get('updatedAt')
    result['updatedAt'] = updated if type(updated) is str and re.fullmatch(
        r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', updated) else None
    # eta also carries arbitrary worker errors. Keep the public progress output
    # numeric; the private log remains available for local diagnosis.
    return result


def worker_running(install_dir):
    """Match an owner worker for this installation, not just a reusable PID."""
    script = Path(install_dir) / 'scripts/bootstrap-upgrade.sh'
    match = re.escape(str(script)) + '.*' + re.escape(str(install_dir))
    result = subprocess.run(['/usr/bin/pgrep', '-u', str(os.getuid()), '-f', match],
        capture_output=True, text=True, timeout=10, check=False)
    if result.returncode not in (0, 1):
        raise ValueError('model-worker-observation-failed')
    return result.returncode == 0


def dashboard_url(install_dir):
    spec = importlib.util.spec_from_file_location('progress_continuation', HERE / 'pixel-native-continuation.py')
    continuation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(continuation)
    values, _ = continuation._saved_environment(install_dir, {'DASHBOARD_PORT'},
        'duplicate-dashboard-port')
    port = values.get('DASHBOARD_PORT', '3001')
    if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
        raise ValueError('invalid-dashboard-port')
    return 'http://127.0.0.1:' + str(int(port)) + '/'


def display(record):
    state = record['status']
    if state == 'not-recorded':
        return 'No model progress has been recorded.'
    text = LABELS[state]
    if record.get('percent') is not None:
        text += ' — ' + format(record['percent'], '.1f') + '%'
    downloaded, total = record.get('bytesDownloaded'), record.get('bytesTotal')
    if downloaded is not None and total:
        text += ' (' + format(downloaded / 1e9, '.2f') + ' / ' + format(total / 1e9, '.2f') + ' GB)'
    if state == 'complete':
        text += '. Verify the active model and a response in Portal.'
    return text


def watch(install_dir, *, observe=progress, running=worker_running, pause=time.sleep):
    print('Watching model progress. Ctrl+C stops this display only; the worker continues.', flush=True)
    previous = None
    try:
        while True:
            record = observe(install_dir)
            alive = running(install_dir)
            terminal = record['status'] in ('complete', 'failed')
            # A newly admitted worker may not yet have replaced a previous
            # attempt's terminal status. Follow the actual worker to its exit.
            message = ('The model worker is still running; waiting for its final status.'
                       if terminal and alive else display(record))
            if message != previous:
                print(message, flush=True)
                previous = message
            if record['status'] == 'complete' and not alive:
                return 0
            if record['status'] == 'failed' and not alive:
                print('Review ' + str(Path(install_dir) / 'logs/model-upgrade.log') + ' locally for the failure.')
                return 1
            if not alive:
                print('No matching model worker is running. Progress does not prove completion; no restart was attempted.')
                return 1
            pause(5)
    except KeyboardInterrupt:
        print('\nStopped displaying progress. ODS and the model worker were left running.')
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if args.watch and args.json:
        parser.error('--watch cannot be combined with --json')
    if sys.platform != 'darwin' or os.geteuid() == 0:
        parser.error('Run as the signed-in macOS owner.')
    try:
        install_dir = Path(args.install_dir).expanduser().resolve(strict=True)
        if args.watch:
            return watch(install_dir)
        record = progress(install_dir)
        record['workerRunning'] = worker_running(install_dir)
        if args.json:
            print(json.dumps(record))
        elif record['workerRunning'] and record['status'] in ('complete', 'failed'):
            print('A model worker is running. Its recorded final status may belong to an earlier attempt; use progress --watch.')
        else:
            print(display(record))
            if not record['workerRunning'] and record['status'] not in ('complete', 'failed'):
                print('No matching model worker is running. The recorded progress does not prove completion.')
        if not args.json:
            print('Portal: ' + dashboard_url(install_dir))
        return 0 if record['workerRunning'] or record['status'] == 'complete' else 1
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        code = str(error) if type(error) is ValueError and str(error) in ERRORS else type(error).__name__
        detail = ' [' + code + ']'
        if isinstance(error, OSError) and type(error.errno) is int:
            detail += ' errno=' + str(error.errno)
        print('Could not read model progress.' + detail + ' The installation and worker were left unchanged.',
            file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
