"""Direct unit tests for cluster-setup-listener.py helpers.

Exercised in the test-runner container with a tmp config; does NOT touch
the running controller instance.
"""
import importlib.util
import json
import os
import pathlib
import tempfile


def _load_listener():
    path = "/app/scripts/cluster-setup-listener.py"
    spec = importlib.util.spec_from_file_location("setup_listener", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_add_worker_writes_atomic_config(tmp_path):
    cfg = tmp_path / "cluster.json"
    cfg.write_text(json.dumps({"nodes": []}))
    mod = _load_listener()

    ok, reason = mod.add_worker_to_config(str(cfg), "10.0.0.2", 50052, "nvidia",
                                          [{"name": "A100", "vram_mb": 81920}])
    assert ok is True
    assert reason == "ok"

    state = json.loads(cfg.read_text())
    assert len(state["nodes"]) == 1
    n = state["nodes"][0]
    assert n["ip"] == "10.0.0.2"
    assert n["rpc_port"] == 50052
    assert n["gpu_backend"] == "nvidia"
    assert n["gpus"][0]["name"] == "A100"
    assert n["status"] == "online"
    assert "added_at" in n


def test_add_worker_dedupe(tmp_path):
    cfg = tmp_path / "cluster.json"
    cfg.write_text(json.dumps({"nodes": []}))
    mod = _load_listener()

    assert mod.add_worker_to_config(str(cfg), "10.0.0.3", 50052, "amd", [])[0] is True
    ok, reason = mod.add_worker_to_config(str(cfg), "10.0.0.3", 50052, "amd", [])
    assert ok is False
    assert reason == "already registered"

    state = json.loads(cfg.read_text())
    assert len(state["nodes"]) == 1


def test_add_worker_distinct_ports_kept_separate(tmp_path):
    cfg = tmp_path / "cluster.json"
    cfg.write_text(json.dumps({"nodes": []}))
    mod = _load_listener()

    assert mod.add_worker_to_config(str(cfg), "10.0.0.4", 50052, "cpu", [])[0] is True
    assert mod.add_worker_to_config(str(cfg), "10.0.0.4", 50053, "cpu", [])[0] is True
    state = json.loads(cfg.read_text())
    assert len(state["nodes"]) == 2


def test_format_gpu_info_single():
    mod = _load_listener()
    s = mod.format_gpu_info([{"name": "RTX 4090", "vram_mb": 24576}])
    assert "RTX 4090" in s
    assert "24.0" in s or "24" in s


def test_format_gpu_info_multi():
    mod = _load_listener()
    s = mod.format_gpu_info([
        {"name": "MI300X", "vram_mb": 196608},
        {"name": "MI300X", "vram_mb": 196608},
    ])
    assert s.count("MI300X") == 2


def test_format_gpu_info_empty():
    mod = _load_listener()
    assert mod.format_gpu_info([]) == "no GPU info"


def test_format_gpu_info_no_vram():
    mod = _load_listener()
    s = mod.format_gpu_info([{"name": "CPU only", "vram_mb": 0}])
    assert s == "CPU only"


def test_recv_json_newline_delimited():
    import socket
    import threading
    mod = _load_listener()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def client():
        c = socket.create_connection(("127.0.0.1", port))
        c.sendall(b'{"hello":1}\nTRAILING-IGNORED')
        c.close()

    t = threading.Thread(target=client, daemon=True)
    t.start()
    conn, _ = srv.accept()
    msg = mod.recv_json(conn, timeout=5)
    conn.close()
    srv.close()
    t.join(timeout=2)
    assert msg == {"hello": 1}
