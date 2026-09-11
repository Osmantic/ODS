"""Platform storage selection for host-owned provider Settings only.

No runtime activation or migration is implied. Native Windows requires local
fixed NTFS and explicit owner-private state; it never adopts or repairs ACLs.
Other provider runtimes retain their existing, separately qualified contracts.
"""
from contextlib import contextmanager
import os
from pathlib import Path

from .store import ProviderStore, StoreError
from .vault import CredentialStore
from . import windows_custody as W


@contextmanager
def _native_errors():
    try:
        yield
    except W.WindowsCustodyError as error:
        code = {'unsupported-platform': 'unsupported-platform',
                'creation-outcome-unknown': 'write-durability-unknown'}.get(
                    error.code, 'storage-unavailable')
        raise StoreError(code) from None


def provider_directory(data_dir):
    if os.name == 'posix':
        return Path(data_dir) / 'pixel-providers'
    if os.name == 'nt':
        with _native_errors():
            # Validate the ORIGINAL spelling, including a terminal dot component,
            # before Path normalization or appending the feature directory.
            value, _, _ = W._path(data_dir)
            return W._path(value + ('' if value.endswith('\\') else '\\') + 'pixel-providers')[0]
    raise StoreError('unsupported-platform')


def provider_store(directory):
    if os.name == 'posix':
        return ProviderStore(directory)
    if os.name == 'nt':
        from .windows_store import WindowsProviderStore
        return WindowsProviderStore(directory)
    raise StoreError('unsupported-platform')


def credential_store(directory):
    if os.name == 'posix':
        return CredentialStore(directory)
    if os.name == 'nt':
        from .windows_vault import WindowsCredentialStore
        return WindowsCredentialStore(directory)
    raise StoreError('unsupported-platform')


@contextmanager
def existing_directory(directory):
    if os.name == 'posix':
        try:
            directory.lstat()
        except FileNotFoundError:
            yield False
        else:
            yield True
    elif os.name == 'nt':
        with _native_errors(), W.open_private(directory, directory=True, missing_ok=True) as handle:
            # Keep existing root custody through the caller's independent store
            # load. A missing final component never creates a root or lock.
            yield handle is not None
    else:
        raise StoreError('unsupported-platform')


def prepare_directory(directory):
    if os.name == 'posix':
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
    elif os.name == 'nt':
        from .windows_bootstrap import create_private_root
        with _native_errors():
            try:
                create_private_root(directory)
            except W.WindowsCustodyError as error:
                if error.code != 'already-exists':
                    raise
                # An existing root is NOT trusted here. The following native
                # transaction independently validates it before any write.
    else:
        raise StoreError('unsupported-platform')
