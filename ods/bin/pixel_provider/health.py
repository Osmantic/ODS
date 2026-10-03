"""Read-only, bounded probe of the applied leader; never activates a provider."""
import http.client
import os
from pathlib import Path
import platform
import socket
import ssl
import sys
import threading
from urllib.parse import urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def normalize_health(value):
    if type(value) is not dict:
        raise ValueError("invalid-provider-health")
    if value.get("status") == "online":
        if (set(value) != {"status", "models"} or type(value["models"]) is not int
                or not 1 <= value["models"] <= 4096):
            raise ValueError("invalid-provider-health")
    elif set(value) != {"status"} or value["status"] not in ("offline", "unavailable", "inactive"):
        raise ValueError("invalid-provider-health")
    return dict(value)


def probe_provider(provider, credential):
    from pixel_provider.connection_transport import _target
    from pixel_provider.store import MAX_BYTES, StoreError, decode_document
    parts = urlsplit(provider["baseUrl"])
    client = sock = None
    try:
        address, port = _target(parts)
        sock = socket.create_connection((address, port), timeout=3)
        if parts.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
        sock.settimeout(3)
        client = http.client.HTTPConnection(parts.hostname, port, timeout=3)
        client.sock = sock
        headers = {"Accept": "application/json", "Connection": "close"}
        if credential:
            headers["Authorization"] = "Bearer " + credential
        # Fixed suffix of an owner-saved URL; no redirects, environment proxies,
        # user-supplied request URLs, generation calls, or credential readback.
        client.request("GET", parts.path.rstrip("/") + "/models", headers=headers)
        response = client.getresponse()
        if response.status != 200:
            return {"status": "offline"}
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return {"status": "unavailable"}
        value = decode_document(raw)
        models = value.get("data") if type(value) is dict else None
        if (type(models) is not list or len(models) > 4096
                or any(type(item) is not dict or type(item.get("id")) is not str for item in models)):
            return {"status": "unavailable"}
        if not any(item["id"] == provider["model"] for item in models):
            return {"status": "offline"}
        return {"status": "online", "models": len(models)}
    except (OSError, ValueError, http.client.HTTPException, StoreError):
        return {"status": "offline"}
    finally:
        if client is not None:
            client.close()
        elif sock is not None:
            sock.close()


def inspect_active_health(data_dir):
    from pixel_provider.host_api import runtime_status
    from pixel_provider.store_factory import credential_store, provider_directory
    runtime = runtime_status(data_dir)
    if runtime["status"] in ("inactive", "not-applied"):
        return {"status": "inactive"}
    if runtime["status"] != "applied":
        return {"status": "unavailable"}
    store = credential_store(provider_directory(data_dir))
    config = store.load()
    if config["revision"] != runtime["binding"]["revision"] or not config["enabled"]:
        return {"status": "unavailable"}
    provider = next((item for item in config["providers"] if item["id"] == config["roles"]["leader"]), None)
    if (not provider or not provider["enabled"]
            or provider["kind"] == "cloud" and not config["policy"]["allowCloud"]):
        return {"status": "inactive"}
    credential = store.resolve_credential(provider["id"], expected_revision=config["revision"])
    result = probe_provider(provider, credential)
    # Do not attach old connectivity evidence to a newly changed active route.
    current = runtime_status(data_dir)
    if (current["status"] != "applied" or current["binding"] != runtime["binding"]
            or store.load()["revision"] != config["revision"]):
        return {"status": "unavailable"}
    return result


def health_status(data_dir):
    if platform.system() != "Linux":
        return {"status": "unavailable"}
    from pixel_provider.advice_process import run_worker
    from pixel_provider.store import StoreError
    try:
        result = run_worker(
            [sys.executable, "-I", "-B", str(Path(__file__).resolve())],
            {"dataDir": str(data_dir)}, cancelled=lambda: False, deadline_seconds=8)
        return normalize_health(result)
    except (StoreError, OSError, ValueError):
        return {"status": "unavailable"}


def main():
    from pixel_provider.advice_frames import read_frame, encode_frame
    try:
        with os.fdopen(os.dup(0), "rb", buffering=0) as stream:
            request = read_frame(stream)
        if (type(request) is not dict or set(request) != {"dataDir"}
                or type(request["dataDir"]) is not str or not os.path.isabs(request["dataDir"])):
            return 2
        def parent_watch():
            os.read(0, 1)
            os._exit(125)
        threading.Thread(target=parent_watch, daemon=True).start()
        result = normalize_health(inspect_active_health(request["dataDir"]))
        sys.stdout.buffer.write(encode_frame(result))
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        # No upstream response, credentials or private file paths in logs.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
