"""Bounded dataset/report I/O through the active SDK exec tool, never the host.

Only JSON data arrives in argv. Reads and create-only writes stay underneath
the execution workspace using directory descriptors and no-follow opens.
"""
import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
import sys

MAX_SOURCE = 64 * 1024
MAX_ROWS = 128
FLAGS = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)


class DatasetError(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise DatasetError(reason)


def parts(value):
    require(isinstance(value, str) and 0 < len(value) <= 512, 'invalid-path')
    names = value.split('/')
    require(len(names) <= 16 and all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._ -]{0,127}', x)
            and not x.endswith(('.', ' ')) for x in names), 'invalid-path')
    return names


def parent(root, relative, create=False):
    fd = os.dup(root)
    try:
        for name in parts(relative)[:-1]:
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(name, FLAGS | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read_source(root, name):
    directory = parent(root, name)
    try:
        fd = os.open(parts(name)[-1], FLAGS, dir_fd=directory)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                    and before.st_size <= MAX_SOURCE, 'unsafe-or-large-source')
            with os.fdopen(os.dup(fd), 'rb') as stream:
                data = stream.read(MAX_SOURCE + 1)
            require(len(data) <= MAX_SOURCE and identity(before) == identity(os.fstat(fd))
                    == identity(os.stat(parts(name)[-1], dir_fd=directory, follow_symlinks=False)),
                    'source-changed')
            return data
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate-json-field')
        result[key] = value
    return result


def dataset(data, source):
    text = data.decode('utf-8-sig', errors='strict')
    require('\0' not in text, 'invalid-source-text')
    name = source['path'].lower()
    if name.endswith('.json'):
        rows = json.loads(text, object_pairs_hook=object_pairs)
    elif name.endswith('.jsonl'):
        rows = [json.loads(line, object_pairs_hook=object_pairs) for line in text.splitlines() if line.strip()]
    else:
        require(name.endswith(('.csv', '.tsv')), 'unsupported-source-format')
        parsed = list(csv.reader(io.StringIO(text, newline=''), delimiter='\t' if name.endswith('.tsv') else ',', strict=True))
        require(bool(parsed) and len(set(parsed[0])) == len(parsed[0])
                and all(parsed[0]), 'invalid-csv-header')
        header = parsed[0]
        require(all(len(row) == len(header) for row in parsed[1:]), 'invalid-csv-row')
        rows = [dict(zip(header, row)) for row in parsed[1:]]
    require(isinstance(rows, list) and 1 <= len(rows) <= MAX_ROWS, 'invalid-row-count')
    items, ids = [], set()
    for index, row in enumerate(rows):
        require(isinstance(row, dict), 'invalid-row')
        value = row.get(source['textColumn'])
        identifier = row.get(source['idColumn']) if source.get('idColumn') else str(index + 1)
        require(isinstance(identifier, (str, int)) and not isinstance(identifier, bool), 'invalid-row-id')
        identifier = str(identifier)
        require(0 < len(identifier) <= 128 and not any(ord(x) < 32 for x in identifier)
                and identifier not in ids, 'invalid-or-duplicate-row-id')
        require(isinstance(value, str) and 0 < len(value.strip()) and len(value) <= 20000, 'invalid-row-text')
        ids.add(identifier)
        items.append({'id': 'r' + str(index + 1), 'sourceId': identifier, 'text': value})
    return items


def source_snapshot(root, source):
    require(isinstance(source, dict) and set(source) <= {'path', 'textColumn', 'idColumn'}
            and {'path', 'textColumn'} <= set(source), 'invalid-source')
    for field in ['textColumn', 'idColumn']:
        require(field not in source or isinstance(source[field], str)
                and 0 < len(source[field]) <= 128, 'invalid-column')
    data = read_source(root, source['path'])
    return {'path': source['path'], 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'items': dataset(data, source)}


def spreadsheet_cell(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def report_csv(report):
    questions = report['questions']
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    # Keep editable decisions separate from original engine confidence. Model
    # corrections must not inherit statistics belonging to a different label.
    writer.writerow(['id'] + [question['id'] for question in questions])
    for item in report['items']:
        cells = [spreadsheet_cell(item['sourceId'])]
        for question in questions:
            answer = item['answers'][question['id']]
            value = answer['choice'] if question['type'] == 'choice' else (
                answer['score'] if question['type'] == 'score' else answer['probability'])
            cells.append(spreadsheet_cell(value))
        writer.writerow(cells)
    return stream.getvalue().encode('utf-8')


def write_new(directory, name, data):
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(os.dup(fd), 'wb') as stream:
            stream.write(data)
            stream.flush()
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(fd), 'rb') as stream:
            require(stream.read(len(data) + 1) == data, 'report-readback-failed')
    finally:
        os.close(fd)
    return {'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def run(payload, workspace='.'):
    require(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW'), 'posix-workspace-required')
    require(isinstance(payload, dict) and payload.get('operation') in ('read', 'write'), 'invalid-operation')
    root = os.open(workspace, FLAGS | os.O_DIRECTORY)
    try:
        snapshot = source_snapshot(root, payload['source'])
        if payload['operation'] == 'read':
            return {'status': 'succeeded', 'source': snapshot}
        report = payload['report']
        require(report['source'] == {key: snapshot[key] for key in ('path', 'bytes', 'sha256')}, 'source-changed')
        require([(x['id'], x['sourceId']) for x in report['items']] ==
                [(x['id'], x['sourceId']) for x in snapshot['items']], 'report-rows-mismatch')
        generation = payload['generation']
        require(isinstance(generation, str) and re.fullmatch(r'laya-[a-f0-9]{32}', generation), 'invalid-generation')
        relative = payload['outputDirectory'] + '/' + generation
        holder = parent(root, relative, create=True)
        try:
            os.mkdir(generation, 0o700, dir_fd=holder)
            directory = os.open(generation, FLAGS | os.O_DIRECTORY, dir_fd=holder)
        finally:
            os.close(holder)
        try:
            csv_bytes = report_csv(report)
            json_bytes = (json.dumps(report, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
            require(len(csv_bytes) + len(json_bytes) <= 128 * 1024, 'report-too-large')
            outputs = [write_new(directory, 'report.csv', csv_bytes), write_new(directory, 'decisions.json', json_bytes)]
            os.fsync(directory)
            rebound = parent(root, relative + '/report.csv')
            try:
                require(identity(os.fstat(rebound))[:2] == identity(os.fstat(directory))[:2], 'report-parent-changed')
            finally:
                os.close(rebound)
            return {'status': 'succeeded', 'readbackVerified': True, 'overwritten': False,
                    'outputs': [{**item, 'path': relative + '/' + item['path']} for item in outputs]}
        finally:
            os.close(directory)
    finally:
        os.close(root)


if __name__ == '__main__':
    try:
        import zlib
        request = json.loads(zlib.decompress(base64.b64decode(sys.argv[1], validate=True)))
        print(json.dumps(run(request), ensure_ascii=False, separators=(',', ':')))
    except (DatasetError, OSError, UnicodeError, ValueError, KeyError, TypeError, csv.Error) as error:
        code = str(error) if isinstance(error, DatasetError) else 'dataset-io-failed'
        print(json.dumps({'status': 'failed', 'error': code}))
        sys.exit(1)
