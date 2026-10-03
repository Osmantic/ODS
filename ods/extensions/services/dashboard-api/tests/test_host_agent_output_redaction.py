"""One redactor for process output the host agent returns.

After #6712 (activation rollback keeps a llama-server log excerpt) and #6714
(container output in install errors), each path had its own patterns. None
caught compound names such as ``LITELLM_MASTER_KEY=...``, bare Hugging Face
tokens, JWTs or (in the runtime excerpt) credentials in URLs, while the
runtime excerpt blanked the word after "token" in ``token count: 512`` and
the install diagnostics blanked ``n_prompt_tokens = 13``. Setup hook output
(the install error Pixel reads) passed only a bearer filter, and the
container log viewer had no redaction at all.

Every path now uses ``_redact_credential_text``. The tables below pin what
it removes and what it must leave alone.

Credential-shaped fixtures are assembled at runtime from a digest, so this
file holds no token-shaped literal for the Secret Scan check to report (the
convention in .github/scripts/check-secret-scan-config.py). None of these
values was ever issued.
"""

import base64
import hashlib
import io
import json
import types
from pathlib import Path

import pytest

from test_host_agent_install_rollback import _mod

_FILL = hashlib.sha256(b"ODS redaction fixture; never issued").hexdigest()


def _b64url(text):
    return base64.urlsafe_b64encode(text.encode("ascii")).decode("ascii").rstrip("=")


def _pem(kind, body, end=True):
    fence = "-----{} " + kind + " PRIVATE KEY-----"
    return fence.format("BEGIN") + "\n" + body + ("\n" + fence.format("END") if end else "")


JWT = ".".join([_b64url('{"alg":"HS256"}'), _b64url('{"sub":"1234567890"}'), _b64url(_FILL)[:43]])
JWT_SIGNATURE = JWT.rsplit(".", 1)[1]
HF = "hf_" + _FILL[:34]
MASTER_KEY = "sk-ods-" + _FILL[:32]
MASTER_TAIL = MASTER_KEY[7:23]
PROJECT_KEY = "sk-proj-" + _FILL[32:54]
ANTHROPIC_KEY = "sk-ant-api03-" + _FILL[10:26]
GITHUB_TOKEN = "ghp_" + _FILL[:36]
GITHUB_PAT = "github_pat_" + _FILL[:22] + "_" + _FILL[22:44]
SLACK_TOKEN = "xoxb-" + _FILL[40:50] + "-" + _FILL[50:60]
GOOGLE_KEY = "AIza" + _b64url(_FILL)[:35]
KEY_BODY = _b64url(_FILL)[8:28]
HEX = _FILL[:24]
SETUP_CODE = "setup" + _FILL[:10]
CONFIGURED_KEY = "cfg" + _FILL[20:32]

# (case id, text, the credential that must not survive)
REDACTED_CASES = [
    ("prefixed-key-env", f"LITELLM_MASTER_KEY={MASTER_KEY}", MASTER_TAIL),
    ("prefixed-key-yaml", f"  OPENAI_API_KEY: {PROJECT_KEY}", PROJECT_KEY[8:20]),
    ("export-quoted", f'export ANTHROPIC_API_KEY="ant-{HEX}"', HEX),
    ("secret-suffix", f"LANGFUSE_NEXTAUTH_SECRET={HEX}", HEX),
    ("token-suffix", f"HUGINN_SECRET_TOKEN=tok_{HEX}", HEX),
    ("password-suffix", "db: LANGFUSE_DB_PASSWORD=hunter2hunter2", "hunter2hunter2"),
    ("secret-key-base", f"DOCUSEAL_SECRET_KEY_BASE={HEX}", HEX),
    ("env-pass", "SMTP_PASS=mailpass99", "mailpass99"),
    ("numeric-password", "FRIGATE_RTSP_PASSWORD=12345678", "12345678"),
    ("json", '{"api_key": "abc123secret", "model": "qwen"}', "abc123secret"),
    ("python-repr", "{'client_secret': 'cs-9988776655'}", "cs-9988776655"),
    ("header", f"x-api-key: {HEX}", HEX),
    ("camel-case", f"apiKey=live{HEX}", HEX),
    ("plural-keys", "api_keys: abc123,def456", "abc123"),
    ("litellm-config", "general_settings.master_key: sk-1234abcd", "sk-1234abcd"),
    ("query-string", "GET /v1/models?token=q-secret-77&x=1 HTTP/1.1", "q-secret-77"),
    ("after-other-name", "error: password=hunter2 rejected", "hunter2"),
    ("hf-bare", f"downloading with {HF} now", HF),
    ("hf-env", f"HF_TOKEN={HF}", HF),
    ("url-user-pass", "connect postgres://ods:hunter2@db:5432/app failed", "hunter2"),
    ("url-srv", "mongodb+srv://admin:s3cr3t@cluster0.example.net/db", "s3cr3t"),
    ("url-token-only", f"https://{GITHUB_TOKEN}@github.com/o/r.git", GITHUB_TOKEN[4:16]),
    ("jwt-bare", f"session {JWT} expired", JWT_SIGNATURE),
    ("jwt-named", f"id_token={JWT}", JWT_SIGNATURE),
    ("bearer-header", f"Authorization: Bearer {HEX}", HEX),
    ("basic-header", "authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
    ("bearer-bare", "curl -H 'Bearer tok-0123456789' http://x", "tok-0123456789"),
    ("cookie", "Cookie: session=abc123def; theme=dark", "abc123def"),
    ("flag-space", f"main: invalid argument --api-key sk-live-{HEX}", HEX),
    ("flag-equals", f"llama-server --hf-token={HEX} --port 8080", HEX),
    ("sk-bare", f"using {ANTHROPIC_KEY}", ANTHROPIC_KEY[13:]),
    ("github-pat", GITHUB_PAT, GITHUB_PAT[34:]),
    ("slack", SLACK_TOKEN, SLACK_TOKEN[16:]),
    ("google", f"key {GOOGLE_KEY}", GOOGLE_KEY[:17]),
    ("private-key", _pem("RSA", KEY_BODY), KEY_BODY),
    ("private-key-cut", _pem("OPENSSH", KEY_BODY, end=False), KEY_BODY),
    ("terminal-colors", "\x1b[31mDB_PASSWORD=hunter2\x1b[0m", "hunter2"),
    ("digits-only", "SECRET_KEY=987654321098765432", "987654321098765432"),
    ("digits-only-long", "API_KEY=123456789012345678", "123456789012345678"),
    ("digits-only-qualified", "HUGINN_SECRET_TOKEN=5566778899", "5566778899"),
    ("vault-approle", "VAULT_SECRET_ID=hunter2hunter2", "hunter2hunter2"),
    ("vault-approle-yaml", "secret_id: hunter2hunter2", "hunter2hunter2"),
    ("url-after-word", "foo_postgres://user:hunter2@host/db", "hunter2"),
    ("url-after-digit", "1postgres://user:hunter2@host/db", "hunter2"),
    ("cookie-later", "Cookie: theme=dark; session=abc123def456", "abc123def456"),
    ("escape-between", "DB_PASSWORD\x1b(B=hunter2", "hunter2"),
]

# Shapes main already redacted on the build path, the rollback excerpt or both
# (review of #6721): JVM system properties, flags with an underscore or a
# single dash, dotted config paths, escaped JSON and unclosed quotes. Each is
# checked on every path. escaped-json-name is new; main missed it everywhere.
EVERY_PATH_CASES = [
    ("jvm-property", "-Dspring.datasource.password=hunter2hunter2", "hunter2hunter2"),
    ("jvm-property-dotted", "-Des.bootstrap.password=hunter2hunter2", "hunter2hunter2"),
    ("jvm-property-dashed", "-Dkc.db-password=hunter2hunter2", "hunter2hunter2"),
    ("flag-underscore", "--api_key=hunter2hunter2", "hunter2hunter2"),
    ("flag-underscore-hf", "--hf_token=hunter2hunter2", "hunter2hunter2"),
    ("flag-underscore-space", "--api_key hunter2hunter2", "hunter2hunter2"),
    ("flag-single-dash", "-password=hunter2hunter2", "hunter2hunter2"),
    ("flag-db", "--db_password=hunter2hunter2", "hunter2hunter2"),
    ("dotted-config-path", "model_list[0].litellm_params.api_key: hunter2hunter2", "hunter2hunter2"),
    ("escaped-json", '{"msg":"export DB_PASSWORD=\\"hunter2hunter2\\""}', "hunter2hunter2"),
    ("escaped-json-name", '{\\"api_key\\": \\"hunter2hunter2\\"}', "hunter2hunter2"),
    ("unclosed-quote", 'DB_PASSWORD="hunter2hunter2', "hunter2hunter2"),
    ("unclosed-escaped-quote", 'DB_PASSWORD=\\"hunter2hunter2', "hunter2hunter2"),
    # Second review: a log prefix ending in a separator (INFO:, root:) used to
    # take "token " as an auth scheme, so the token name never matched.
    ("logger-level-token", "INFO:     token = hunter2hunter2", "hunter2hunter2"),
    ("logger-name-token", "INFO:root:token = hunter2hunter2", "hunter2hunter2"),
    ("compose-prefix-token", "app | INFO: token = hunter2hunter2", "hunter2hunter2"),
    ("error-prefix-token", "error: token = hunter2hunter2", "hunter2hunter2"),
    ("tab-before-separator", "INFO: token\t= hunter2hunter2", "hunter2hunter2"),
    ("credential-logger-token", "DEBUG:app.auth:token : hunter2hunter2", "hunter2hunter2"),
    ("credential-logger-password", "DEBUG:app.auth:password : hunter2hunter2", "hunter2hunter2"),
    ("bearer-assignment", "worker | WARNING: bearer = hunter2hunter2", "hunter2hunter2"),
    ("flag-extra-dash", "---password hunter2hunter2", "hunter2hunter2"),
    ("jvm-property-extra-dash", "--Dapi_key=hunter2hunter2", "hunter2hunter2"),
    # New: main missed this one everywhere.
    ("token-name-after-credential", "password: token : hunter2hunter2", "hunter2hunter2"),
]
REDACTED_CASES += EVERY_PATH_CASES

# Ordinary log text that must come back unchanged.
KEPT_CASES = [
    "keyboard layout: us",
    "keyboard=us",
    "error: token count: 512 exceeds the context",
    "load: token to piece cache size = 0.9310 MB",
    "print_info: EOS token        = 151645 '<|im_end|>'",
    "slot update_slots: id  0 | task 0 | n_prompt_tokens = 13, n_ctx_slot = 40960",
    "max_tokens: 512",
    "max_token=4096",
    "tokenizer.ggml.bos_token_id u32 = 151643",
    "bos_token_id=151643",
    "srv load_model: the slot context (131072) exceeds the training context of the model (40960) - capping",
    "sha256:f9b8432be04e320406157e26c3ff52a7e9a4bea7eabe5477e9636791737eb119",
    "FROM docker.swagger.io/swaggerapi/swagger-ui:v5.33.0@sha256:"
    "f9b8432be04e320406157e26c3ff52a7e9a4bea7eabe5477e9636791737eb119",
    "Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf",
    "model=unsloth/Qwen3.5-27B-GGUF:Q4_K_M",
    "TOKEN_SPY_PORT=3005",
    "TOKEN_SPY_URL=http://token-spy:8080",
    "PIXEL_GATEWAY_TOKEN_FILE=/run/secrets/pixel-gateway-token",
    "LIGHTRAG_EMBEDDING_TOKEN_LIMIT=8192",
    "api_key_header: X-Api-Key",
    "sort_key: name",
    "sort_keys: name",
    "public_key: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGx",
    "OTHER_FLAG_KEY=true debug=true",
    "hf_hub_download failed for hf_transfer",
    "Missing bearer token",
    "src/token.rs:12:5: error in token::Token",
    "secret.py:42: warning",
    "Invalid API key provided",
    'The "LITELLM_MASTER_KEY" variable is not set. Defaulting to a blank string.',
    "required variable GOTIFY_ADMIN_PASSWORD is missing a value",
    "git@github.com:Osmantic/ODS.git",
    "api_key=None",
    "password: ${DB_PASSWORD}",
    "main: server is listening on http://0.0.0.0:8080",
    "pip install scikit-learn",
    "eos_token: <|im_end|>",
    "stop token: </s>",
    "print_info: EOT token        = 151645 '<|im_end|>'",
    "Authorization: Bearer",
    "Cookie:",
    'missing "DB_PASSWORD=" line in .env',
    "INFO:     token = 151645",
    "INFO:root:token count = 512",
    "bearerFormat: JWT",
    "token_type: bearer",
]


@pytest.mark.parametrize("text,secret", [case[1:] for case in REDACTED_CASES],
                         ids=[case[0] for case in REDACTED_CASES])
def test_credentials_are_redacted(text, secret):
    redacted = _mod._redact_credential_text(text)
    assert secret not in redacted
    assert "[REDACTED]" in redacted
    assert _mod._redact_credential_text(redacted) == redacted  # Idempotent.


@pytest.mark.parametrize("text", KEPT_CASES)
def test_ordinary_log_text_is_kept(text):
    assert _mod._redact_credential_text(text) == text


def test_names_and_structure_stay_readable():
    assert _mod._redact_credential_text(f"LITELLM_MASTER_KEY={MASTER_KEY}") == "LITELLM_MASTER_KEY=[REDACTED]"
    assert _mod._redact_credential_text('{"token": "abc123"}') == '{"token": "[REDACTED]"}'
    assert _mod._redact_credential_text("Authorization: Bearer abc.def.ghi") == "Authorization: Bearer [REDACTED]"
    assert _mod._redact_credential_text("postgres://u:p@db/app") == "postgres://[REDACTED]@db/app"
    # Every cookie in a Cookie header, not only the first.
    assert _mod._redact_credential_text("Cookie: session=abc123; theme=dark") == "Cookie: [REDACTED]"
    assert _mod._redact_credential_text("curl -H 'Cookie: a=1; b=2' http://x") == "curl -H 'Cookie: [REDACTED]' http://x"
    assert _mod._redact_credential_text("{cookie: sid=abc123, accept: text/html}") == (
        "{cookie: [REDACTED], accept: text/html}")
    assert _mod._redact_credential_text('{"set-cookie": ["sid=abc123; Path=/"]}') == (
        '{"set-cookie": ["[REDACTED]"]}')
    assert _mod._redact_credential_text("-Dspring.datasource.password=hunter2") == (
        "-Dspring.datasource.password=[REDACTED]")
    assert _mod._redact_credential_text('{"msg":"export DB_PASSWORD=\\"hunter2\\""}') == (
        '{"msg":"export DB_PASSWORD=\\"[REDACTED]\\""}')
    assert _mod._redact_credential_text('DB_PASSWORD="hunter2') == "DB_PASSWORD=[REDACTED]"
    assert _mod._redact_credential_text("api_key=, model=qwen") == "api_key=, model=qwen"


def test_auth_scheme_is_skipped_only_after_a_credential_name():
    # After Authorization the scheme word stays and the value after it goes.
    assert _mod._redact_credential_text(f"Authorization: token {GITHUB_TOKEN}") == "Authorization: token [REDACTED]"
    assert _mod._redact_credential_text(f"Authorization: token {HEX}") == "Authorization: token [REDACTED]"
    assert _mod._redact_credential_text(f"Authorization: Bearer {HEX}") == "Authorization: Bearer [REDACTED]"
    assert _mod._redact_credential_text(f"api_key: token {HEX}") == "api_key: token [REDACTED]"
    # After a log prefix, "token" is the name and keeps its own separator.
    assert _mod._redact_credential_text("INFO:     token = hunter2") == "INFO:     token = [REDACTED]"
    assert _mod._redact_credential_text("INFO:root:token = hunter2") == "INFO:root:token = [REDACTED]"
    # A word followed by a separator is the next name, never a scheme, even
    # right after a credential name: both values go.
    assert _mod._redact_credential_text("DEBUG:app.auth:token : hunter2") == "DEBUG:app.auth:[REDACTED] : [REDACTED]"
    assert _mod._redact_credential_text("password: token : hunter2") == "password: [REDACTED] : [REDACTED]"
    assert _mod._redact_credential_text("WARNING: bearer = hunter2") == "WARNING: bearer = [REDACTED]"


def test_known_values_and_control_characters():
    text = "\x1b[1mconnecting as shlink with dbsecretvalue123\x07\x1b[0m\r\nready\ttrue"
    assert _mod._redact_credential_text(text, ["dbsecretvalue123", "", None]) == (
        "connecting as shlink with [REDACTED]\nready\ttrue")
    assert _mod._redact_credential_text(None) == ""


def test_escapes_are_removed_before_matching():
    # tput sgr0 prints ESC ( B, which used to sit between the name and the value.
    assert _mod._redact_credential_text("DB_PASSWORD\x1b(B\x1b[m=hunter2") == "DB_PASSWORD=[REDACTED]"
    assert _mod._redact_credential_text("\x1b]0;window title\x07ready") == "ready"


def test_carriage_returns_keep_progress_lines_apart():
    assert _mod._redact_credential_text("10%\r50%\r100%\r\ndone") == "10%\n50%\n100%\ndone"
    assert _mod._runtime_log_excerpt("error: first\rerror: second") == "error: first\nerror: second"


@pytest.mark.parametrize("line,secret", [case[1:] for case in EVERY_PATH_CASES],
                         ids=[case[0] for case in EVERY_PATH_CASES])
def test_review_shapes_are_redacted_on_every_path(line, secret, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)

    def docker(args, **kwargs):
        if args[:2] == ["docker", "logs"]:
            return types.SimpleNamespace(returncode=0, stdout=line)
        return types.SimpleNamespace(returncode=1, stdout="")
    monkeypatch.setattr(_mod.subprocess, "run", docker)

    outputs = {
        "build": _mod._install_build_diagnostic(
            types.SimpleNamespace(stderr=line + "\nfailed to solve: exit code: 1"), {}),
        "container": _mod._collect_container_start_diagnostic("ods-x", {}, None),
        "rollback excerpt": _mod._runtime_log_excerpt("error: " + line),
        "log viewer": _mod._redact_credential_text(line),
    }
    for path, output in outputs.items():
        assert secret not in output, path
        assert "[REDACTED]" in output, path


def test_credential_fixtures_are_built_at_runtime():
    """Secret Scan reads the whole history, so no fixture may be a literal here."""
    source = Path(__file__).read_text(encoding="utf-8")
    for value in (JWT, HF, MASTER_KEY, PROJECT_KEY, ANTHROPIC_KEY, GITHUB_TOKEN, GITHUB_PAT,
                  SLACK_TOKEN, GOOGLE_KEY, JWT.split(".")[0], "-----" + "BEGIN"):
        assert value not in source


def test_runtime_log_excerpt_redacts_the_new_shapes_and_keeps_counts():
    log = "\n".join([
        "print_info: EOS token        = 151645 '<|im_end|>'",
        f"error: LITELLM_MASTER_KEY={MASTER_KEY} rejected",
        f"warn: fetching with {HF}",
        "error: upstream postgres://ods:hunter2@db:5432/app refused",
        f"error: invalid session {JWT}",
        "error: token count: 512 exceeds the context",
        "srv load_model: the slot context (131072) exceeds the training context of the model (40960) - capping",
    ])
    excerpt = _mod._runtime_log_excerpt(log)
    for secret in (MASTER_TAIL, HF, "hunter2", JWT_SIGNATURE):
        assert secret not in excerpt
    assert "error: token count: 512 exceeds the context" in excerpt
    assert "exceeds the training context of the model (40960) - capping" in excerpt
    assert "EOS token" not in excerpt  # Not a signal line; selection is unchanged.


def test_build_diagnostic_uses_the_same_redactor(tmp_path, monkeypatch):
    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
    output = "\n".join([
        f"LITELLM_MASTER_KEY={MASTER_KEY}",
        f"Downloading {HF}",
        f"Authorization header {JWT}",
        "slot update_slots: n_prompt_tokens = 13",
        "failed to solve: exit code: 1",
    ])
    diagnostic = _mod._install_build_diagnostic(types.SimpleNamespace(stderr=output), {})
    for secret in (MASTER_TAIL, HF, JWT_SIGNATURE):
        assert secret not in diagnostic
    assert "n_prompt_tokens = 13" in diagnostic
    assert diagnostic.startswith("Untrusted build error: failed to solve: exit code: 1\n")


def test_progress_errors_use_the_same_redactor(tmp_path, monkeypatch):
    monkeypatch.setattr(_mod, "DATA_DIR", tmp_path)
    _mod._write_progress("demo", "error", "Installation failed",
                         error=f"compose up failed: HF_TOKEN={HF} Bearer abc123def456 token count: 3")
    record = json.loads((tmp_path / "extension-progress" / "demo.json").read_text(encoding="utf-8"))
    assert record["error"] == ("compose up failed: HF_TOKEN=[REDACTED] Bearer [REDACTED] token count: 3")


def test_setup_hook_output_is_redacted_before_it_becomes_the_install_error(tmp_path, monkeypatch):
    install_dir, ext_dir = tmp_path / "install", tmp_path / "hooked"
    install_dir.mkdir()
    ext_dir.mkdir()
    (install_dir / ".env").write_text(f"HOOKED_SETUP_CODE={SETUP_CODE}\nOTHER_API_KEY={CONFIGURED_KEY}\n",
                                      encoding="utf-8")
    (ext_dir / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    manifest = {"service": {"id": "hooked", "port": 8080, "env_vars": [
        {"key": "HOOKED_SETUP_CODE", "secret": True}]}}
    monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(_mod, "_resolve_hook", lambda directory, name: directory / "setup.sh")
    monkeypatch.setattr(_mod, "_read_manifest", lambda directory: manifest)
    monkeypatch.setattr(_mod, "_find_usable_bash", lambda: "bash")
    stderr = (f"generated {SETUP_CODE} for the admin\nusing {CONFIGURED_KEY}\n"
              f"LITELLM_MASTER_KEY={MASTER_KEY}\n{HF}\n"
              "openssl: not found\n")
    monkeypatch.setattr(_mod.subprocess, "run",
                        lambda *args, **kwargs: types.SimpleNamespace(returncode=1, stdout="", stderr=stderr))
    progress = []
    monkeypatch.setattr(_mod, "_write_progress", lambda *args, **kwargs: progress.append((args, kwargs)))

    ok, message = _mod._run_post_install_hook("hooked", ext_dir)

    assert ok is False
    for secret in (SETUP_CODE, CONFIGURED_KEY, MASTER_TAIL, HF):
        assert secret not in message
    assert message.rstrip().endswith("openssl: not found")
    assert progress[-1][1]["error"] == message


def test_setup_hook_output_is_withheld_when_redaction_cannot_run(tmp_path, monkeypatch):
    ext_dir = tmp_path / "hooked"
    ext_dir.mkdir()
    monkeypatch.setattr(_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(_mod, "_resolve_hook", lambda directory, name: directory / "setup.sh")
    monkeypatch.setattr(_mod, "_read_manifest", lambda directory: {"service": {"id": "hooked"}})
    monkeypatch.setattr(_mod, "_find_usable_bash", lambda: "bash")
    monkeypatch.setattr(_mod.subprocess, "run",
                        lambda *args, **kwargs: types.SimpleNamespace(returncode=1, stdout="", stderr="secret-ish"))
    monkeypatch.setattr(_mod, "_write_progress", lambda *args, **kwargs: None)

    def unreadable(service_def, directory=None):
        raise OSError("unreadable .env")
    monkeypatch.setattr(_mod, "_declared_secret_values", unreadable)

    ok, message = _mod._run_post_install_hook("hooked", ext_dir)

    assert ok is False
    assert message == "Setup hook output withheld: credential redaction could not be completed."


class _Handler:
    def __init__(self, body):
        payload = json.dumps(body).encode("utf-8")
        self.rfile = io.BytesIO(payload)
        self.wfile = io.BytesIO()
        self.headers = {"Authorization": "Bearer agent-key", "Content-Length": str(len(payload))}
        self.code = None

    def send_response(self, code):
        self.code = code

    def send_header(self, *_args):
        pass

    def end_headers(self):
        pass

    def body(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


CONTAINER_LOG = (f"starting with HF_TOKEN={HF}\n"
                 "DATABASE_URL=postgres://app:hunter2@db/app\n"
                 "prompt processed: n_prompt_tokens = 13\n")


@pytest.mark.parametrize("handler_name", ["_handle_logs", "_handle_service_logs"])
def test_log_viewer_redacts_container_output(monkeypatch, handler_name):
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "agent-key")
    monkeypatch.setattr(_mod, "validate_service_id", lambda handler, body: body["service_id"])
    monkeypatch.setattr(_mod, "_resolve_container_name", lambda sid: f"ods-{sid}")
    monkeypatch.setattr(_mod.subprocess, "run",
                        lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stdout=CONTAINER_LOG))
    handler = _Handler({"service_id": "demo", "tail": 50})

    getattr(_mod.AgentHandler, handler_name)(handler)

    assert handler.code == 200
    logs = handler.body()["logs"]
    assert HF not in logs and "hunter2" not in logs
    assert "HF_TOKEN=[REDACTED]" in logs and "postgres://[REDACTED]@db/app" in logs
    assert "n_prompt_tokens = 13" in logs


def test_log_viewer_failure_message_is_redacted(monkeypatch):
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "agent-key")
    monkeypatch.setattr(_mod, "_resolve_container_name", lambda sid: f"ods-{sid}")
    monkeypatch.setattr(_mod.subprocess, "run", lambda *args, **kwargs: types.SimpleNamespace(
        returncode=1, stdout=f"error from daemon: HF_TOKEN={HF}"))
    handler = _Handler({"service_id": "demo"})

    _mod.AgentHandler._handle_service_logs(handler)

    assert handler.code == 500
    assert handler.body()["error"] == "docker logs failed: error from daemon: HF_TOKEN=[REDACTED]"
