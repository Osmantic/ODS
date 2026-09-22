"""Expose only explicitly imported database snapshots using native immutable mode."""
import os
from pathlib import Path

root = Path('/data')
databases = sorted(p for p in root.iterdir() if p.suffix.lower() in {'.db', '.sqlite', '.sqlite3'})
names = set()
args = ['datasette', 'serve', '--host', '0.0.0.0', '--port', '8001']
for path in databases:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Import a regular SQLite snapshot: {path.name}')
    if path.stem in names:
        raise ValueError(f'Duplicate database name: {path.stem}')
    names.add(path.stem)
    with path.open('rb') as database:
        if database.read(16) != b'SQLite format 3\x00':
            raise ValueError(f'Not a SQLite database: {path.name}')
    args.extend(['--immutable', str(path)])
for key, value in {
    'sql_time_limit_ms': '1000',
    'max_returned_rows': '1000',
    'allow_download': 'off',
    'allow_csv_stream': 'off',
}.items():
    args.extend(['--setting', key, value])
os.execvp(args[0], args)
