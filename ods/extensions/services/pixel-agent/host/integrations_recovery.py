"""Permission-reducing review path for one host-latched recovery run.

The private request field is supplied by an exact native dispatch ticket, never
by the model schema. This path has no actuator: known builtin reads may run;
opaque calls and supported mutations may only prepare an exact Ask request.
It never consumes a grant, changes settings, starts custom MCP, or sends ntfy.
"""
import json
import re
import uuid

import integrations_notes as notes
from integrations_app_service import APP_SERVER_IDS, app_text
from integrations_common import IntegrationError, clip, digest, now, redact_secrets

REVIEW_FIELD = "recoveryReviewOnly"
REVIEW_APPROVAL_SECONDS = 24 * 3600
BUILTIN_READS = frozenset({("documents", "search"), ("documents", "read"),
                           ("photos", "search"), ("watch", "list"),
                           ("news", "unread"), ("news", "digest")})


def review_only_request(request):
    """Omission is normal; a present field cannot ask to downgrade recovery."""
    if REVIEW_FIELD not in request:
        return False
    if request[REVIEW_FIELD] is not True:
        raise IntegrationError("invalid-review-control", "The private recovery control is invalid. Nothing was done.")
    return True


class RecoveryReviewOnly:
    def _review_only_agent(self, action, request):
        if action in {"notes-search", "notes-read", "mcp-tools"}:
            # Only these literal read handlers may re-enter normal dispatch.
            normal = {key: value for key, value in request.items() if key != REVIEW_FIELD}
            return {**self._agent(action, normal), REVIEW_FIELD: True}
        if action == "notes-write":
            return self._review_notes_write(request)
        if action == "mcp-call":
            return self._review_mcp_call(request)
        # Includes notify, cron/task-finished and digest management callbacks.
        raise IntegrationError("review-only-refused", "This recovery can only read builtin connections or prepare an Ask request. "
                               "No action or notification was performed.")

    @staticmethod
    def _review_binding(config, kind, server_id=None):
        bindings = config.get("approvalBindings", {})
        value = bindings.get("notes") if kind == "notes" else bindings.get(kind, {}).get(server_id)
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
            raise IntegrationError("review-only-context-unavailable", "This connection has no current saved review identity. "
                                   "Review and save its settings before retrying; no request was created.")
        return value

    @staticmethod
    def _require_review_ask(policy):
        if policy != "ask":
            raise IntegrationError("review-only-policy", "Automatic recovery cannot use Allowed or Off to perform an action. "
                                   "It can only prepare a request under Ask first. No action or approval request was created.")

    def _review_approval(self, kind, summary, request_digest):
        """Caller holds current policy/generation admission; never consumes a grant."""
        with self.store.lock:
            approvals = self.store.approvals()  # Existing 24-hour expiry semantics.
            exact = [entry for entry in approvals if isinstance(entry, dict) and entry.get("digest") == request_digest]
            # An approved grant is not a pending request and is never consumed.
            approved = next((entry for entry in exact if entry.get("state") == "approved"), None)
            if approved:
                return {"status": "review-only-ready", "approvalId": approved["id"], "summary": approved["summary"],
                        REVIEW_FIELD: True, "actionExecuted": False, "approvalConsumed": False}
            if any(entry.get("state") == "declined" for entry in exact):
                raise IntegrationError("review-only-declined", "The exact request was declined. This recovery did not reopen it "
                                       "or perform the action.")
            if any(entry.get("state") not in {"pending", "used"} for entry in exact):
                raise IntegrationError("review-only-state-unavailable", "The saved request state cannot be reviewed safely. Nothing was done.")
            pending = next((entry for entry in exact if entry.get("state") == "pending"), None)
            if pending is None:
                summary = redact_secrets(summary, self.store.secrets())[:400]
                pending = {"id": uuid.uuid4().hex[:12], "kind": kind, "summary": summary, "digest": request_digest,
                           "state": "pending", "createdAt": now(), "expiresAt": now() + REVIEW_APPROVAL_SECONDS}
                self.store.save_approvals(approvals + [pending])
                self._log("approval", summary, "requested for review")
            # No _notify_approval: even an approval ping would be an external effect.
            return {"status": "approval-required", "approvalId": pending["id"], "summary": pending["summary"],
                    REVIEW_FIELD: True, "actionExecuted": False, "approvalConsumed": False}

    def _review_notes_write(self, request):
        with self.store.lock:
            config = self.store.config()
            if not config["notes"]["enabled"]:
                raise IntegrationError("notes-off", "Obsidian notes are not connected. This recovery did not change a note.")
            self._require_review_ask(config["notes"]["write"])
            mode = request.get("mode", "append")
            notes.validate_write(request.get("text"), mode)
            relative, target = notes.write_binding(config, str(request.get("path", "")), mode)
            epoch = self._review_binding(config, "notes")
            summary = f"{'Add to' if mode == 'append' else 'Create'} note {relative}"
            return self._review_approval("note", summary,
                                         digest("note-v2", epoch, target, relative, mode, request.get("text")))

    def _review_mcp_call(self, request):
        name, arguments = request.get("tool"), request.get("arguments", {})
        if not isinstance(arguments, dict):
            raise IntegrationError("invalid-request", "Tool arguments must be an object.")
        server_id = request.get("server")
        if server_id in APP_SERVER_IDS:
            app = APP_SERVER_IDS[server_id]
            self._app_arguments(app, name, arguments)
            if app == "watch" and name in {"add", "remove"}:
                return self._review_watch_change(name, arguments)
            if (app, name) not in BUILTIN_READS:
                raise IntegrationError("review-only-refused", "That builtin action is unavailable during review-only recovery.")
            with self.store.lock:
                config, secrets = self.store.config(), self.store.secrets()
                # Verify the captured endpoint/key together without _binding writes.
                self._verified_app_snapshot(config, secrets, app)
            # In particular, digest reads cannot arm a later delivery snapshot.
            action, reply = self.use_app(config, app, name, arguments, run_context=None, secrets=secrets)
            return {"server": server_id, "tool": name,
                    "text": clip(redact_secrets(app_text(action, reply), secrets)), "isError": False, REVIEW_FIELD: True}
        with self.store.lock:
            config, secrets = self.store.config(), self.store.secrets()
            server = self._server(config, server_id, enabled=True)
            context = digest(self._recovery_server_context(server, secrets))
            if server.get("checkedBinding") != context:
                raise IntegrationError("mcp-check-required", "The server context changed. Check its tools before using it.")
            tool = next((entry for entry in server["tools"] if entry["name"] == name), None)
            if tool is None:
                raise IntegrationError("tool-not-allowed", "That saved MCP tool is not available. List the tools first.")
            # readOnly hints do not turn an opaque server call into a builtin read.
            self._require_review_ask(tool.get("policy"))
            epoch = self._review_binding(config, "mcp", server["id"])
            argument_summary = redact_secrets(json.dumps(arguments, sort_keys=True), secrets)[:300]
            summary = redact_secrets(f"{server['name']}: {name} {argument_summary}", secrets)
            return self._review_approval("mcp", summary,
                                         digest("mcp-v2", epoch, context, tool, name, arguments))
