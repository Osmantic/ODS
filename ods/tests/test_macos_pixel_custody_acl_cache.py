"""The cached library must never cache the ACL result for an open file."""
import ctypes
import errno
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import pixel_macos_custody as custody  # noqa: E402


def test_acl_is_queried_again_when_library_bindings_are_reused(tmp_path, monkeypatch):
    def absent(_fd, _kind):
        ctypes.set_errno(errno.ENOENT)
        return None

    library = SimpleNamespace(acl_get_fd_np=Mock(),
        acl_valid=Mock(return_value=0), acl_get_entry=Mock(return_value=0), acl_free=Mock())
    library.acl_get_fd_np.side_effect = lambda fd, kind: absent(fd, kind) if library.acl_get_fd_np.call_count == 1 else 1
    loader = Mock(return_value=library)
    monkeypatch.setattr(custody.sys, 'platform', 'darwin')
    monkeypatch.setattr(custody.ctypes, 'CDLL', loader)
    custody._acl_library.cache_clear()
    try:
        with (tmp_path / 'file').open('w+b') as file:
            custody._require_no_acl(file.fileno())
            with pytest.raises(custody.CustodyError, match='acl-present'):
                custody._require_no_acl(file.fileno())
        assert loader.call_count == 1
        assert library.acl_get_fd_np.call_count == 2
        library.acl_free.assert_called_once_with(1)
    finally:
        custody._acl_library.cache_clear()


@pytest.mark.parametrize('error', [errno.EACCES, errno.EIO])
def test_acl_query_errors_still_fail_closed(tmp_path, monkeypatch, error):
    def unavailable(_fd, _kind):
        ctypes.set_errno(error)
        return None
    library = SimpleNamespace(acl_get_fd_np=Mock(side_effect=unavailable),
        acl_valid=Mock(), acl_get_entry=Mock(), acl_free=Mock())
    monkeypatch.setattr(custody.sys, 'platform', 'darwin')
    monkeypatch.setattr(custody.ctypes, 'CDLL', Mock(return_value=library))
    custody._acl_library.cache_clear()
    try:
        with (tmp_path / 'file').open('w+b') as file:
            with pytest.raises(custody.CustodyError, match='acl-unavailable'):
                custody._require_no_acl(file.fileno())
    finally:
        custody._acl_library.cache_clear()
