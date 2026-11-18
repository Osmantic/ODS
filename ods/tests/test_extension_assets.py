"""Model packs an extension's container reads beside its image (bin/extension_assets.py).

The prepare step and the installer fetch them; these tests drive the module
with a fake HTTP opener, so nothing leaves the machine.
"""
import errno
import hashlib
import http.client
import io
import json
import os
import shutil
import ssl
import sys
import urllib.error
from pathlib import Path

import pytest

ODS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ODS / "bin"))
import extension_assets as assets  # noqa: E402

PAYLOAD = b"model weights " * 4096
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def _pack_list(tmp_path, *, sha256=DIGEST, size=len(PAYLOAD), default=True, root="data/comfyui/models"):
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir(exist_ok=True)
    (ext_dir / "model-packs.json").write_text(json.dumps({
        "schema": "ods.model-packs.v1",
        "model_root": {"nvidia": root, "amd": "data/comfyui/ComfyUI/models"},
        "packs": [{"id": "image-default", "label": "Test model", "feature": "images", "default": default,
                   "files": [{"subdir": "checkpoints", "file": "test.safetensors",
                              "url": "https://example.invalid/test.safetensors",
                              "sha256": sha256, "bytes": size, "license": "test"}]}],
    }), encoding="utf-8")
    install = tmp_path / "install"
    install.mkdir(exist_ok=True)
    return ext_dir, install


class Response(io.BytesIO):
    def __init__(self, body, status=200, fail_after=None, failure=None):
        super().__init__(body)
        self.status = status
        self.fail_after, self.failure = fail_after, failure

    def read(self, size=-1):
        if self.fail_after is not None and self.tell() >= self.fail_after:
            raise self.failure
        return super().read(size if self.fail_after is None else min(size, self.fail_after - self.tell()))


def opener_for(body, status=200, seen=None):
    def opener(request, timeout):
        if seen is not None:
            seen.append((dict(request.header_items()), timeout))
        return Response(body, status)
    return opener


def opener_sequence(*answers, seen=None):
    """Answer each request with the next Response, or raise the next exception."""
    pending = list(answers)

    def opener(request, timeout):
        if seen is not None:
            seen.append(dict(request.header_items()))
        answer = pending.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer
    return opener


def no_network(request, timeout):
    raise AssertionError("fetch made a request it did not need")


def _http_error(code, reason):
    return urllib.error.HTTPError("https://example.invalid/test.safetensors", code, reason, {}, None)


def test_default_files_follow_the_backend_and_skip_optional_packs(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    assert item.target == install.resolve() / "data/comfyui/models/checkpoints/test.safetensors"
    assert (item.size, item.sha256, item.label) == (len(PAYLOAD), DIGEST, "Test model")
    [amd] = assets.default_files(ext_dir, install, "amd")
    assert amd.target.parent == install.resolve() / "data/comfyui/ComfyUI/models/checkpoints"
    assert assets.default_files(ext_dir, install, "cpu") == []
    assert assets.default_files(tmp_path / "no-packs", install, "nvidia") == []
    ext_dir, install = _pack_list(tmp_path, default=False)
    assert assets.default_files(ext_dir, install, "nvidia") == []


@pytest.mark.parametrize("change", [
    {"root": "../outside"},
    {"root": "data/../../outside"},
    {"root": "/etc/ods"},
    {"root": ""},
    {"sha256": "ABC"},
    {"size": 0},
])
def test_unsafe_or_incomplete_pack_lists_are_refused(tmp_path, change):
    ext_dir, install = _pack_list(tmp_path, **change)
    with pytest.raises(ValueError):
        assets.default_files(ext_dir, install, "nvidia")


def test_a_data_folder_linked_to_another_disk_is_accepted(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    elsewhere = tmp_path / "other-disk"
    elsewhere.mkdir()
    try:
        (install / "data").symlink_to(elsewhere, target_is_directory=True)
    except OSError as exc:  # Windows without symlink rights
        pytest.skip(f"cannot create a symlink here: {exc}")
    [item] = assets.default_files(ext_dir, install, "nvidia")
    assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD))
    assert (elsewhere / "comfyui/models/checkpoints/test.safetensors").read_bytes() == PAYLOAD


def test_a_malformed_pack_list_is_a_value_error(tmp_path):
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()
    (ext_dir / "model-packs.json").write_text('{"schema": "ods.model-packs.v1", "packs": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        assets.default_files(ext_dir, tmp_path, "nvidia")


def test_a_file_counts_as_present_only_at_its_pinned_size(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    assert assets.missing([item]) == [item]
    item.target.parent.mkdir(parents=True)
    item.target.write_bytes(PAYLOAD[:100])
    assert assets.missing([item]) == [item]
    item.target.write_bytes(PAYLOAD)
    assert assets.missing([item]) == []


def test_fetch_downloads_verifies_and_renames(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    progress, seen = [], []
    assets.fetch(item, progress.append, opener=opener_for(PAYLOAD, seen=seen))
    assert item.target.read_bytes() == PAYLOAD
    assert not item.target.with_name("test.safetensors.part").exists()
    assert progress[-1] == len(PAYLOAD)
    headers, timeout = seen[0]
    assert "Range" not in headers and headers["User-agent"] == assets.USER_AGENT
    assert timeout == assets.READ_TIMEOUT_SECONDS


def test_fetch_resumes_a_partial_download_with_a_range_request(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    part = item.target.with_name("test.safetensors.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(PAYLOAD[:1000])
    seen = []
    assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD[1000:], status=206, seen=seen))
    assert seen[0][0]["Range"] == "bytes=1000-"
    assert item.target.read_bytes() == PAYLOAD


def test_fetch_starts_over_when_the_server_ignores_the_range(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    part = item.target.with_name("test.safetensors.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(b"x" * 1000)  # not even the right bytes
    assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD, status=200))
    assert item.target.read_bytes() == PAYLOAD


def test_a_wrong_checksum_deletes_the_download(tmp_path):
    ext_dir, install = _pack_list(tmp_path, sha256="0" * 64)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    with pytest.raises(assets.AssetError, match="did not match its pinned SHA-256"):
        assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD))
    assert not item.target.exists()
    assert not item.target.with_name("test.safetensors.part").exists()


def test_a_short_download_keeps_the_part_for_a_resume(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    with pytest.raises(assets.AssetError, match="Try again to resume it"):
        assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD[:5000]))
    assert item.target.with_name("test.safetensors.part").stat().st_size == 5000
    assert not item.target.exists()


def test_a_network_failure_names_the_cause_and_keeps_the_part(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")

    def offline(request, timeout):
        raise urllib.error.URLError("Temporary failure in name resolution")

    with pytest.raises(assets.AssetError, match="Temporary failure in name resolution") as caught:
        assets.fetch(item, lambda done: None, opener=offline)
    assert caught.value.retryable


def test_a_connection_dropped_mid_transfer_keeps_what_arrived(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    dropped = Response(PAYLOAD, fail_after=7000, failure=http.client.IncompleteRead(b""))
    with pytest.raises(assets.AssetError, match="Try again to resume it") as caught:
        assets.fetch(item, lambda done: None, opener=opener_sequence(dropped))
    assert caught.value.retryable
    assert item.target.with_name("test.safetensors.part").read_bytes() == PAYLOAD[:7000]


def test_a_complete_part_is_checked_and_renamed_without_downloading(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    part = item.target.with_name("test.safetensors.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(PAYLOAD)
    assets.fetch(item, lambda done: None, opener=no_network)
    assert item.target.read_bytes() == PAYLOAD and not part.exists()


def test_a_complete_part_with_the_wrong_bytes_is_deleted(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    part = item.target.with_name("test.safetensors.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(b"y" * len(PAYLOAD))
    with pytest.raises(assets.AssetError, match="did not match its pinned SHA-256") as caught:
        assets.fetch(item, lambda done: None, opener=no_network)
    assert not caught.value.retryable and not part.exists()


def test_a_part_larger_than_the_file_starts_over(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    part = item.target.with_name("test.safetensors.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(PAYLOAD + b"extra")
    assert assets._remaining(item) == len(PAYLOAD)
    seen = []
    assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD, seen=seen))
    assert "Range" not in seen[0][0]
    assert item.target.read_bytes() == PAYLOAD


def test_a_refused_range_deletes_the_part_instead_of_looping(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    part = item.target.with_name("test.safetensors.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(PAYLOAD[:1000])
    with pytest.raises(assets.AssetError, match="not the pinned file") as caught:
        assets.fetch(item, lambda done: None,
                     opener=opener_sequence(_http_error(416, "Range Not Satisfiable")))
    assert not caught.value.retryable
    assert part.stat().st_size == 0


def test_http_errors_say_whether_trying_again_can_help(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    with pytest.raises(assets.AssetError) as busy:
        assets.fetch(item, lambda done: None, opener=opener_sequence(_http_error(503, "Service Unavailable")))
    assert busy.value.retryable
    with pytest.raises(assets.AssetError, match=r"link for Test model no longer works \(HTTP 404\)") as gone:
        assets.fetch(item, lambda done: None, opener=opener_sequence(_http_error(404, "Not Found")))
    assert not gone.value.retryable
    with pytest.raises(assets.AssetError, match=r"refused Test model \(HTTP 403 Forbidden\).*example\.invalid") as no:
        assets.fetch(item, lambda done: None, opener=opener_sequence(_http_error(403, "Forbidden")))
    assert not no.value.retryable


def test_a_certificate_that_fails_to_verify_is_not_retried(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    bad_certificate = urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed"))
    with pytest.raises(assets.AssetError, match="Check the system clock and any proxy") as caught:
        assets.fetch(item, lambda done: None, opener=opener_sequence(bad_certificate))
    assert not caught.value.retryable


def _denied(*args, **kwargs):
    raise PermissionError(errno.EACCES, "Permission denied", str(args[0]))


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="file ownership is POSIX")
def test_a_folder_owned_by_another_account_says_how_to_fix_it(tmp_path, monkeypatch):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    uid, gid = os.getuid(), os.getgid()
    monkeypatch.setattr(assets, "open", _denied, raising=False)
    monkeypatch.setattr(assets.os, "access", lambda path, mode: False)
    monkeypatch.setattr(assets.os, "getuid", lambda: uid + 1)  # the folder is now someone else's
    with pytest.raises(assets.AssetError, match="belongs to another account") as caught:
        assets.fetch(item, lambda done: None, opener=no_network)
    message = str(caught.value)
    assert message.endswith(f"sudo chown -R {uid + 1}:{gid} {item.target.parent}")
    assert not caught.value.retryable and "Try again to resume" not in message


def test_a_write_refused_in_your_own_folder_does_not_blame_its_owner(tmp_path, monkeypatch):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    monkeypatch.setattr(assets, "open", _denied, raising=False)
    with pytest.raises(assets.AssetError, match="no other program, such as an antivirus") as caught:
        assets.fetch(item, lambda done: None, opener=no_network)
    message = str(caught.value)
    assert str(item.target.parent) in message
    assert "sudo chown" not in message and "another account" not in message


def test_a_longer_answer_is_cut_at_the_pinned_size(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD + b"junk" * 50000))
    assert item.target.read_bytes() == PAYLOAD


def test_bytes_another_writer_appended_fail_the_check(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    part = item.target.with_name("test.safetensors.part")

    class Interleaved(Response):
        def read(self, size=-1):
            if self.tell() == 0:  # a second writer, with no lock to stop it, appends too
                with open(part, "ab") as other:
                    other.write(b"X" * 100)
            return super().read(size)

    with pytest.raises(assets.AssetError, match="did not match its pinned SHA-256"):
        assets.fetch(item, lambda done: None, opener=opener_sequence(Interleaved(PAYLOAD)))
    assert not item.target.exists() and not part.exists()


def test_a_file_finished_meanwhile_is_not_downloaded_again(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    item.target.parent.mkdir(parents=True)
    item.target.write_bytes(PAYLOAD)
    assets.fetch(item, lambda done: None, opener=no_network)
    assert not item.target.with_name("test.safetensors.part").exists()


@pytest.mark.skipif(assets.fcntl is None, reason="the folder lock uses fcntl (POSIX)")
def test_a_file_system_without_locks_still_downloads(tmp_path, monkeypatch):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")

    def unsupported(fd, operation):
        raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    monkeypatch.setattr(assets.fcntl, "flock", unsupported)
    assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD))
    assert item.target.read_bytes() == PAYLOAD


def test_a_full_disk_while_writing_is_reported_as_the_disk(tmp_path, monkeypatch):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    real_open = open

    class FullDisk:
        def __init__(self, path, mode):
            self.handle = real_open(path, mode)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

        def write(self, data):
            raise OSError(errno.ENOSPC, "No space left on device")

        def __getattr__(self, name):
            return getattr(self.handle, name)

    monkeypatch.setattr(assets, "open", FullDisk, raising=False)
    with pytest.raises(assets.AssetError, match=r"could not save Test model .*No space left") as caught:
        assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD))
    assert not caught.value.retryable


@pytest.mark.skipif(assets.fcntl is None, reason="the folder lock uses fcntl (POSIX)")
def test_a_second_download_into_the_same_folder_is_refused(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    item.target.parent.mkdir(parents=True)
    holder = os.open(item.target.parent, os.O_RDONLY)
    try:
        assets.fcntl.flock(holder, assets.fcntl.LOCK_EX | assets.fcntl.LOCK_NB)
        with pytest.raises(assets.AssetError, match="already downloading"):
            assets.fetch(item, lambda done: None, opener=no_network)
    finally:
        os.close(holder)
    assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD))
    assert item.target.read_bytes() == PAYLOAD


def test_space_check_counts_partial_downloads_and_refuses_with_numbers(tmp_path, monkeypatch):
    ext_dir, install = _pack_list(tmp_path, size=10 * 10 ** 9)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(assets.shutil, "disk_usage",
                        lambda path: usage._replace(free=5 * 10 ** 9))
    with pytest.raises(assets.AssetError, match=r"needs 10\.0 GB plus 2\.1 GB to spare.*has 5\.0 GB free"):
        assets.check_space([item])
    monkeypatch.setattr(assets.shutil, "disk_usage",
                        lambda path: usage._replace(free=13 * 10 ** 9))
    assets.check_space([item])


def test_the_command_line_fetches_what_is_missing(tmp_path, monkeypatch, capsys):
    ext_dir, install = _pack_list(tmp_path)
    monkeypatch.setattr(assets.urllib.request, "urlopen", opener_for(PAYLOAD))
    assert assets.main([str(ext_dir), str(install), "nvidia"]) == 0
    assert "Test model is ready (SHA-256 verified)" in capsys.readouterr().out
    assert assets.main([str(ext_dir), str(install), "nvidia"]) == 0
    assert "already in place" in capsys.readouterr().out


def test_the_command_line_reports_a_failure(tmp_path, monkeypatch, capsys):
    ext_dir, install = _pack_list(tmp_path, sha256="0" * 64)
    monkeypatch.setattr(assets.urllib.request, "urlopen", opener_for(PAYLOAD))
    assert assets.main([str(ext_dir), str(install), "nvidia"]) == 1
    assert "ERROR:" in capsys.readouterr().err


def test_the_command_line_resumes_through_dropped_connections(tmp_path, monkeypatch, capsys):
    ext_dir, install = _pack_list(tmp_path)
    seen = []
    monkeypatch.setattr(assets.urllib.request, "urlopen", opener_sequence(
        OSError("connect failed"),
        Response(PAYLOAD[:5000]),
        Response(PAYLOAD[5000:], status=206),
        seen=seen))
    assert assets.main([str(ext_dir), str(install), "nvidia", "--attempts", "3", "--retry-delay", "0"]) == 0
    assert "Range" not in seen[1] and seen[2]["Range"] == "bytes=5000-"
    out = capsys.readouterr().out
    assert "dropped (OSError); resuming in 0 s (try 2 of 3)" in out
    assert "dropped (ended early); resuming in 0 s (try 3 of 3)" in out and "Test model is ready" in out
    # The installer summary would read these words as a failure.
    assert not any(word in out for word in ("ERROR", "FAILED", "failed"))


def test_the_command_line_does_not_retry_what_a_retry_cannot_fix(tmp_path, monkeypatch, capsys):
    ext_dir, install = _pack_list(tmp_path, sha256="0" * 64)
    seen = []
    monkeypatch.setattr(assets.urllib.request, "urlopen", opener_sequence(Response(PAYLOAD), seen=seen))
    assert assets.main([str(ext_dir), str(install), "nvidia", "--attempts", "6", "--retry-delay", "0"]) == 1
    assert len(seen) == 1
    assert "did not match its pinned SHA-256" in capsys.readouterr().err


def test_the_command_line_reports_a_malformed_pack_list(tmp_path, capsys):
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()
    (ext_dir / "model-packs.json").write_text('{"schema": "ods.model-packs.v1", "packs": []}', encoding="utf-8")
    assert assets.main([str(ext_dir), str(tmp_path), "nvidia"]) == 1
    assert capsys.readouterr().err.startswith("ERROR: ")


def test_the_comfyui_pack_list_parses_for_both_gpu_backends(tmp_path):
    comfyui = ODS / "extensions/services/comfyui"
    for backend in ("nvidia", "amd"):
        [item] = assets.default_files(comfyui, tmp_path, backend)
        assert item.target.name == "sdxl_lightning_4step.safetensors"
        assert item.size == 6938040682
