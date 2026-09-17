"""Local gateway selection on real loopback sockets; never installed services."""
import contextlib
import errno
import json
from pathlib import Path
import socket
import socketserver
import struct
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "bin"))
import pixel_access_bridge as bridge

SNAPSHOT = dict(available=True, phase="idle", active=0, pid=123, revision="a" * 64)


@contextlib.contextmanager
def gateways(*families, post_reset=False, snapshot=None):
    seen, servers, threads = [], [], []
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(1)
            raw = b""
            while b"\r\n\r\n" not in raw:
                part = self.request.recv(8192)
                if not part: return
                raw += part
            method = raw.split(b" ", 1)[0].decode()
            seen.append((self.server.address_family, method))
            assert b"Authorization: Bearer fixture" in raw
            if post_reset and method == "POST":
                self.request.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                return
            body = json.dumps(SNAPSHOT if snapshot is None else snapshot).encode()
            self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    try:
        # An ephemeral port free in one address family may be occupied in the
        # other. Reserve both listeners before starting either worker.
        for attempt in range(10):
            port = 0
            try:
                for family in families:
                    class Server(socketserver.ThreadingTCPServer):
                        address_family = family
                        daemon_threads = True
                        def server_bind(self):
                            if self.address_family == socket.AF_INET6:
                                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                            super().server_bind()
                    server = Server(("::1" if family == socket.AF_INET6 else "127.0.0.1", port), Handler)
                    servers.append(server)
                    port = server.server_address[1]
                break
            except OSError as error:
                for server in servers:
                    server.server_close()
                servers.clear()
                if error.errno == errno.EADDRINUSE and attempt < 9:
                    continue
                if family == socket.AF_INET6 and error.errno in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
                    pytest.skip("IPv6 loopback unavailable on this test host")
                raise
        for server in servers:
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            threads.append(thread)
        yield port, seen
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()


def adapter_at(tmp_path, port):
    adapter = bridge.SystemdAccessBridge(tmp_path, "fixture", state=tmp_path / "state")
    adapter.native_port, adapter.native_key = port, "fixture"
    adapter.command = lambda *_args, **_kwargs: "123"
    return adapter


def test_ipv6_is_preferred_and_post_stays_on_qualified_origin(tmp_path):
    with gateways(socket.AF_INET6, socket.AF_INET) as (port, seen):
        adapter = adapter_at(tmp_path, port)
        assert adapter.native("probe", "d" * 64) == SNAPSHOT
        assert seen == [(socket.AF_INET6, "GET"), (socket.AF_INET6, "POST")]
        assert adapter.native_origin == "http://[::1]:%d" % port


def test_ipv4_only_gateway_is_discovered_before_mutation(tmp_path):
    with gateways(socket.AF_INET) as (port, seen):
        adapter = adapter_at(tmp_path, port)
        assert adapter.native("probe", "d" * 64) == SNAPSHOT
        assert seen == [(socket.AF_INET, "GET"), (socket.AF_INET, "POST")]
        assert adapter.native_origin == "http://127.0.0.1:%d" % port


def test_ambiguous_post_reset_never_falls_back_or_replays(tmp_path):
    with gateways(socket.AF_INET6, socket.AF_INET, post_reset=True) as (port, seen):
        adapter = adapter_at(tmp_path, port)
        with pytest.raises(bridge.AccessError, match="runtime-unavailable-or-busy"):
            adapter.native("probe", "d" * 64)
        assert seen == [(socket.AF_INET6, "GET"), (socket.AF_INET6, "POST")]


@pytest.mark.parametrize("drift,code", [({"pid":456}, "gateway-process-mismatch"),
    ({"available":False}, "admission-gate-unavailable"), ({"revision":"bad"}, "admission-gate-unavailable")])
def test_wrong_process_or_invalid_capability_never_redirects_mutation(tmp_path, drift, code):
    with gateways(socket.AF_INET6, socket.AF_INET, snapshot={**SNAPSHOT, **drift}) as (port, seen):
        adapter = adapter_at(tmp_path, port)
        with pytest.raises(bridge.AccessError, match=code): adapter.native("probe", "d" * 64)
        assert seen == [(socket.AF_INET6, "GET")]


def test_gateway_restart_during_discovery_cannot_authorize_post(tmp_path):
    with gateways(socket.AF_INET) as (port, seen):
        adapter = adapter_at(tmp_path, port)
        identities = iter(["123", "124"])
        adapter.command = lambda *_args, **_kwargs: next(identities)
        with pytest.raises(bridge.AccessError, match="gateway-process-mismatch"):
            adapter.native("probe", "d" * 64)
        assert seen == [(socket.AF_INET, "GET")]


def test_new_gateway_pid_discovers_ipv6_again_instead_of_reusing_ipv4(tmp_path, monkeypatch):
    adapter = adapter_at(tmp_path, 18789)
    adapter.native_origin = "http://127.0.0.1:18789"
    adapter._native_identity = (18789, "fixture", 122)
    calls = []
    def http(origin, *_args, **_kwargs):
        calls.append(origin)
        return SNAPSHOT
    monkeypatch.setattr(adapter, "http", http)
    assert adapter.native() == SNAPSHOT
    assert calls == ["http://[::1]:18789"]


def test_discovery_reads_and_post_share_one_deadline(tmp_path, monkeypatch):
    adapter = adapter_at(tmp_path, 18789)
    now, calls = [100.0], []
    monkeypatch.setattr(bridge.time, "monotonic", lambda: now[0])
    def command(*_args, **_kwargs):
        now[0] += .05
        return "123"
    def http(origin, _path, _key, payload=None, *, timeout):
        calls.append((origin, payload, timeout))
        if len(calls) == 1:
            now[0] += timeout
            raise bridge.AccessError("runtime-operation-timeout")
        now[0] += .05
        return SNAPSHOT
    adapter.command = command
    monkeypatch.setattr(adapter, "http", http)
    assert adapter.native("probe", "d" * 64, timeout=.5) == SNAPSHOT
    assert [call[2] for call in calls] == pytest.approx([.225, .225, .125])
    assert calls[2][0] == calls[1][0]
    assert now[0] < 100.5


def test_pinned_409_readback_cannot_fallback_to_another_origin(tmp_path, monkeypatch):
    adapter = adapter_at(tmp_path, 18789)
    calls = []
    def http(origin, _path, _key, payload=None, **_kwargs):
        calls.append((origin, payload))
        if len(calls) == 1: return {**SNAPSHOT, "phase":"held"}
        raise bridge.AccessError("runtime-unavailable-or-busy", http_status=409 if payload else None)
    monkeypatch.setattr(adapter, "http", http)
    monkeypatch.setattr(adapter, "owns_native_hold", lambda *_args: True)
    with pytest.raises(bridge.AccessError): adapter.native("acquire", "d" * 64)
    assert len(calls) == 3
    assert all(origin == "http://[::1]:18789" for origin, _ in calls)
    assert sum(payload is not None for _, payload in calls) == 1
