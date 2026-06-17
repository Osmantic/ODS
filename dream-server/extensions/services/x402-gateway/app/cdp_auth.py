from __future__ import annotations

import base64
import json
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .config import FacilitatorAuthConfig


def _base64url(payload: bytes | str) -> str:
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _load_private_key(raw_base64_secret: str) -> Ed25519PrivateKey:
    raw = base64.b64decode(raw_base64_secret, validate=True)
    if len(raw) != 64:
        raise ValueError(f"expected 64-byte CDP Ed25519 secret, got {len(raw)} bytes")
    return Ed25519PrivateKey.from_private_bytes(raw[:32])


@dataclass(frozen=True)
class CdpJwtSigner:
    key_id: str
    private_key: Ed25519PrivateKey
    host: str
    base_path: str

    def sign(self, method: str, endpoint: str) -> str:
        now = int(time.time())
        header = {
            "alg": "EdDSA",
            "typ": "JWT",
            "kid": self.key_id,
            "nonce": secrets.token_hex(16),
        }
        payload = {
            "iss": "cdp",
            "sub": self.key_id,
            "nbf": now,
            "exp": now + 120,
            "uri": f"{method} {self.host}{self.base_path}{endpoint}",
        }
        signing_input = (
            f"{_base64url(json.dumps(header, separators=(',', ':')))}."
            f"{_base64url(json.dumps(payload, separators=(',', ':')))}"
        )
        signature = self.private_key.sign(signing_input.encode("utf-8"))
        return f"{signing_input}.{_base64url(signature)}"


def create_cdp_auth_headers(
    facilitator_url: str,
    auth_config: FacilitatorAuthConfig,
):
    key_id = os.environ.get(auth_config.apiKeyIdEnv)
    key_secret = os.environ.get(auth_config.apiKeySecretEnv)
    if not key_id or not key_secret:
        raise RuntimeError(
            f"{auth_config.apiKeyIdEnv} and {auth_config.apiKeySecretEnv} "
            "must be set for CDP facilitator auth"
        )

    parsed = urlparse(facilitator_url)
    base_path = parsed.path.rstrip("/")
    signer = CdpJwtSigner(
        key_id=key_id,
        private_key=_load_private_key(key_secret),
        host=parsed.netloc,
        base_path=base_path,
    )

    def headers_for(method: str, endpoint: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {signer.sign(method, endpoint)}"}

    def create_headers() -> dict[str, dict[str, str]]:
        return {
            "verify": headers_for("POST", "/verify"),
            "settle": headers_for("POST", "/settle"),
            "supported": headers_for("GET", "/supported"),
            "bazaar": headers_for("GET", "/bazaar"),
        }

    return create_headers
