"""Permission-reducing review path for one host-latched recovery run.

The private request field is supplied by an exact native dispatch ticket, never
by the model schema. This path has no actuator: known builtin reads may run;
opaque calls and supported mutations may only prepare an exact Ask request.
It never consumes a grant, changes settings, starts custom MCP, or sends ntfy.
"""
import json
import re
import uuid
import urllib.parse

import integrations_notes as notes
from integrations_app_service import APP_SERVER_IDS, app_text
from integrations_common import IntegrationError, clip, digest, now, redact_secrets

REVIEW_FIELD = "recoveryReviewOnly"
REVIEW_APPROVAL_SECONDS = 24 * 3600
BUILTIN_READS = frozenset({("documents", "search"), ("documents", "read"),
                           ("photos", "search"), ("watch", "list"),
                           ("news", "unread"), ("news", "digest")})
REFERENCE_FIELD = "recoveryReferenceContext"
REFERENCE_BYTES = 16 * 1024


def _reference_entry(source, entry):
    if not isinstance(entry, dict):
        return False
    fields = {"obsidian": {"path", "binding"}, "paperless": {"id", "title"}, "watch": {"id", "title", "url"}}
    if set(entry) != fields[source]:
        return False
    if source == "obsidian":
        try:
            canonical = notes._parts(entry["path"])[1]
        except IntegrationError:
            return False
        return canonical == entry["path"] and isinstance(entry["binding"], str) and bool(re.fullmatch(r"[a-f0-9]{64}", entry["binding"]))
    if not isinstance(entry["title"], str) or len(entry["title"]) > 200:
        return False
    identity = entry["id"]
    if source == "paperless":
        return isinstance(identity, str) and bool(re.fullmatch(r"[1-9][0-9]{0,18}", identity, flags=re.ASCII)) and int(identity) <= 2**63 - 1
    if not isinstance(identity, str) or not 0 < len(identity) <= 128 or re.search(r"[\x00-\x1f\x7f]", identity):
        return False
    url = entry["url"]
    if not isinstance(url, str) or not 0 < len(url) <= 500 or re.search(r"[\x00-\x20\x7f]", url):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        parsed.port
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.username is None and parsed.password is None
    except ValueError:
        return False


def _reference_bytes(value, *, wire=False):
    # The factory sends literal UTF-8 JSON; the host replies with ASCII escaping.
    # Bound each actual wire form without accepting malformed Unicode.
    literal = len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    return len(json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")) if wire else literal


def review_only_request(request):
    """Omission is normal; a present field cannot ask to downgrade recovery."""
    if REVIEW_FIELD not in request:
        return False
    if request[REVIEW_FIELD] is not True:
        raise IntegrationError("invalid-review-control", "The private recovery control is invalid. Nothing was done.")
    return True


class RecoveryReviewOnly:
    @staticmethod
    def _review_text(value, limit, *, normalize=False):
        if not isinstance(value, str):
            raise IntegrationError("invalid-request", "Review lookup text must be a string; no constraints were applied.")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise IntegrationError("invalid-request", "Review lookup text must be valid Unicode. Nothing ran.") from None
        effective = " ".join(value.split()) if normalize else value.strip()
        if len(effective if normalize else value) > limit:
            raise IntegrationError("invalid-request", "That review input exceeds the tool's text limit. No lookup or proposal ran.")
        return effective

    def _review_app_values(self, app, name, arguments):
        if (app, name) == ("watch", "add"):
            if not isinstance(arguments.get("url"), str):
                raise IntegrationError("invalid-request", "Give the watched page's web address as a string. No proposal ran.")
            from integrations_apps import valid_watch_url
            valid_watch_url(arguments["url"])
        fields = {("documents", "search"): (("query", 200, True),),
                  ("photos", "search"): (("query", 200, True), ("place", 100, True)),
                  ("watch", "add"): (("title", 200, False),)}.get((app, name), ())
        for field, limit, normalize in fields:
            if field in arguments:
                value = arguments[field]
                # Watch's real host bound applies after stripping outer spaces.
                if app == "watch" and isinstance(value, str):
                    value = value.strip()
                self._review_text(value, limit, normalize=normalize)
    @staticmethod
    def _reference_context(request, source):
        if REFERENCE_FIELD not in request:
            return None
        value = request[REFERENCE_FIELD]
        try:
            valid = (isinstance(value, dict) and set(value) == {"version", "source", "binding", "entries", "truncated"}
                     and type(value["version"]) is int and value["version"] == 1 and value["source"] == source
                     and source in {"obsidian", "paperless", "watch"} and isinstance(value["binding"], str)
                     and bool(re.fullmatch(r"[a-f0-9]{64}", value["binding"])) and type(value["truncated"]) is bool
                     and isinstance(value["entries"], list) and len(value["entries"]) <= 16
                     and all(_reference_entry(source, entry) for entry in value["entries"])
                     and len({entry["path" if source == "obsidian" else "id"] for entry in value["entries"]}) == len(value["entries"])
                     and _reference_bytes(value) <= REFERENCE_BYTES)
        except (ValueError, TypeError, UnicodeError):
            valid = False
        if not valid:
            raise IntegrationError("invalid-review-reference", "The private lookup context is invalid. No lookup or proposal ran.")
        # Detach from a caller's mutable request; this is private factual context,
        # never permission or a new approval token.
        return json.loads(json.dumps(value))

    @staticmethod
    def _reference_envelope(source, binding, entries, secrets, *, truncated=False):
        envelope = {"version": 1, "source": source, "binding": binding, "entries": [], "truncated": bool(truncated)}
        seen = set()
        for raw in entries:
            entry = dict(raw)
            identity = "path" if source == "obsidian" else "id"
            if source == "paperless" and type(entry.get("id")) is int:
                entry["id"] = str(entry["id"])
            if redact_secrets(entry.get(identity), secrets) != entry.get(identity):
                envelope["truncated"] = True
                continue
            if source != "obsidian":
                entry["title"] = redact_secrets(str(entry.get("title") or ""), secrets)[:200]
            if source == "watch":
                # Check raw credentials before clipping could conceal the @.
                raw_url = str(entry.get("url") or "")
                try:
                    parsed = urllib.parse.urlsplit(raw_url)
                    if parsed.username is not None or parsed.password is not None:
                        envelope["truncated"] = True
                        continue
                except ValueError:
                    envelope["truncated"] = True
                    continue
                public_url = redact_secrets(raw_url, secrets)
                if public_url != raw_url or len(public_url) > 500:
                    envelope["truncated"] = True
                    continue
                entry["url"] = public_url
            try:
                valid = _reference_entry(source, entry)
                trial = {**envelope, "entries": envelope["entries"] + [entry]}
                fits = valid and entry[identity] not in seen and len(trial["entries"]) <= 16 and _reference_bytes(trial, wire=True) <= REFERENCE_BYTES
            except (ValueError, TypeError, UnicodeError):
                fits = False
            if fits:
                envelope["entries"].append(entry)
                seen.add(entry[identity])
            else:
                envelope["truncated"] = True
        return envelope

    @staticmethod
    def _app_reference_binding(config, app, key):
        from integrations_common import app_context, watch_context
        pairing = app_context(config, app, key)
        return digest("review-watch-source-v1", pairing, watch_context(config, key)) if app == "watch" else digest("review-paperless-source-v1", pairing)

    def _check_app_reference(self, context, config, app, key):
        if context is not None and context["binding"] != self._app_reference_binding(config, app, key):
            raise IntegrationError("stale-review-reference", "The app connection changed. No lookup or proposal ran.")

    @staticmethod
    def _check_watch_reference_target(context, identity, target):
        expected = next((entry for entry in context["entries"] if entry["id"] == identity), None) if context else None
        if expected is not None and (target is None or expected["url"] != target.get("url")):
            raise IntegrationError("stale-review-reference", "The referenced watched page changed. No proposal was created.")

    def _review_only_agent(self, action, request):
        if action in {"notes-search", "notes-read"}:
            expected = self._reference_context(request, "obsidian")
            with self.store.lock:
                config, secrets = self.store.config(), self.store.secrets()
            if action == "notes-search":
                query = request.get("query", "")
                self._review_text(query, 300)
                hits, binding, entries, truncated = notes.search(config, query, _review=True, reference_context=expected)
                self._log("notes-search", query[:80], f"{len(hits)} notes")
                return {"results": hits, REVIEW_FIELD: True, "reviewReferences":
                        self._reference_envelope("obsidian", binding, entries, secrets, truncated=truncated)}
            relative, text = notes.read(config, request.get("path"), reference_context=expected)
            self._log("notes-read", relative, "read")
            return {"path": relative, "text": text, REVIEW_FIELD: True}
        if action == "mcp-tools":
            self._reference_context(request, None)
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
        expected = self._reference_context(request, "obsidian")
        with self.store.lock:
            config = self.store.config()
            if not config["notes"]["enabled"]:
                raise IntegrationError("notes-off", "Obsidian notes are not connected. This recovery did not change a note.")
            self._require_review_ask(config["notes"]["write"])
            mode = request.get("mode", "append")
            notes.validate_write(request.get("text"), mode)
            relative, target = notes.write_binding(config, str(request.get("path", "")), mode, reference_context=expected)
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
            self._review_app_values(app, name, arguments)
            source = {"documents": "paperless", "watch": "watch"}.get(app)
            expected = self._reference_context(request, source)
            if app == "watch" and name in {"add", "remove"}:
                return self._review_watch_change(name, arguments, reference_context=expected)
            if (app, name) not in BUILTIN_READS:
                raise IntegrationError("review-only-refused", "That builtin action is unavailable during review-only recovery.")
            with self.store.lock:
                config, secrets = self.store.config(), self.store.secrets()
                # Verify the captured endpoint/key together without _binding writes.
                _config, _settings, key = self._verified_app_snapshot(config, secrets, app)
                self._check_app_reference(expected, config, app, key)
            # In particular, digest reads cannot arm a later delivery snapshot.
            action, reply = self.use_app(config, app, name, arguments, run_context=None, secrets=secrets,
                                         review_references=app == "watch" and name == "list", review_only=True)
            result = {"server": server_id, "tool": name,
                    "text": clip(redact_secrets(app_text(action, reply), secrets)), "isError": False, REVIEW_FIELD: True}
            if (app, name) in {("documents", "search"), ("watch", "list")}:
                entries = ([{"id": row["id"], "title": row["title"]} for row in reply["results"]]
                           if app == "documents" else reply["_reviewEntries"])
                truncated = reply["total"] > len(entries)
                result["reviewReferences"] = self._reference_envelope(source, self._app_reference_binding(config, app, key),
                                                                      entries, secrets, truncated=truncated)
            return result
        self._reference_context(request, None)
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
