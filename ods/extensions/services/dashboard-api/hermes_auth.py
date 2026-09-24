"""ODS managed Hermes login; never writes an operator's config or credentials.

Also mounted read-only as the Hermes entrypoint so s6's dashboard inherits the
same defaults as the gateway. Explicit upstream auth configuration wins.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

import yaml


def settings(env=None, config_path=None):
    env = os.environ if env is None else env
    if config_path is None:
        # The API must not need access to another UID's private Hermes home.
        policy_path = Path(env.get("HERMES_AUTH_POLICY_PATH", "/data/hermes-auth/policy.json"))
        try:
            policy = json.loads(policy_path.read_text())
        except (OSError, ValueError):
            policy = {}
        if not env.get("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD"):
            seed_digest = hashlib.sha256(env.get("HERMES_DASHBOARD_SESSION_TOKEN", "").encode()).hexdigest()
            if (policy.get("schemaVersion") != 1 or policy.get("managed") is not True
                    or not hmac.compare_digest(str(policy.get("seedDigest", "")), seed_digest)):
                return None
        config_path = "/nonexistent-ods-hermes-config"
    try:
        config = yaml.safe_load(Path(config_path).read_text()) or {}
    except FileNotFoundError:
        config = {}
    dashboard = config.get("dashboard", {}) or {}
    basic = dashboard.get("basic_auth", {}) or {}
    username = env.get("HERMES_DASHBOARD_BASIC_AUTH_USERNAME") or basic.get("username")
    explicit_password = env.get("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD")
    password_hash = env.get("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH") or basic.get("password_hash")
    password = explicit_password or (basic.get("password") if not password_hash else None)
    if password:
        return {"username": username or "ods", "password": password, "managed": False}
    # A hash cannot be turned into a bridge password. Nor can ODS silently
    # replace an operator's OAuth or other dashboard provider with basic auth.
    custom_auth = any(dashboard.get(k) for k in ("oauth", "self_hosted", "auth", "basic_auth"))
    if (password_hash or custom_auth or env.get("HERMES_DASHBOARD_OAUTH_CLIENT_ID")
            or env.get("HERMES_DASHBOARD_OIDC_CLIENT_ID")
            or env.get("HERMES_ODS_MANAGED_AUTH", "true").lower() == "false"):
        return None
    seed = env.get("HERMES_DASHBOARD_SESSION_TOKEN", "")
    if not seed:
        return None
    def derive(purpose):
        return hmac.new(seed.encode(), ("ods/hermes/" + purpose + "/v1").encode(), hashlib.sha256).hexdigest()
    return {"username": username or "ods", "password": derive("password"),
            "secret": derive("signing"), "managed": True}


def bootstrap():
    config_path = Path(os.environ.get("HERMES_HOME", "/opt/data")) / "config.yaml"
    if not config_path.exists():
        config_path = Path("/opt/hermes/cli-config.yaml.example")
    auth = settings(config_path=config_path)
    if auth and not os.environ.get("HERMES_DASHBOARD_BASIC_AUTH_USERNAME"):
        os.environ["HERMES_DASHBOARD_BASIC_AUTH_USERNAME"] = auth["username"]
    policy_dir = Path(os.environ.get("HERMES_AUTH_POLICY_DIR", "/run/ods-hermes-auth"))
    if policy_dir.is_symlink():
        raise RuntimeError("Hermes auth policy directory must not be a symlink")
    policy_dir.mkdir(parents=True, exist_ok=True)
    if os.getuid() == 0:
        os.chown(policy_dir, 0, 0)
    policy_dir.chmod(0o755)
    policy_tmp = policy_dir / "policy.json.tmp"
    if policy_tmp.is_symlink():
        raise RuntimeError("Hermes auth policy file must not be a symlink")
    policy_tmp.write_text(json.dumps({"schemaVersion": 1, "managed": bool(auth and auth["managed"]),
                                     "seedDigest": hashlib.sha256(os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN", "").encode()).hexdigest()}))
    policy_tmp.chmod(0o644)
    policy_tmp.replace(policy_dir / "policy.json")
    if auth and auth["managed"]:
        for key, value in (("USERNAME", auth["username"]), ("PASSWORD", auth["password"]), ("SECRET", auth["secret"])):
            name = "HERMES_DASHBOARD_BASIC_AUTH_" + key
            if not os.environ.get(name):
                os.environ[name] = value
    # Keep the actual upstream bootstrap/UID remapping/seed-once behavior.
    import sys
    dispatcher = "/opt/hermes/docker/entrypoint-dispatch.sh"
    argv = [dispatcher] if Path(dispatcher).is_file() else ["/init", "/opt/hermes/docker/main-wrapper.sh"]
    os.execv(argv[0], argv + sys.argv[1:])


if __name__ == "__main__":
    bootstrap()
