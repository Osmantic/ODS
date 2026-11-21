import hashlib
import json
import os
import threading

_HASHES = {
    "tui_gateway/methods_session.py": "6510098165745c41f92a53cbe35ec7bbf8cbbf6623ffc535f46e960b5a320623",
    "tui_gateway/session_auto_continue.py": "69dfb58c18fa365fb7c2e9f753e671ad49a071699502d534b2ef9b883573fc34",
    "tui_gateway/server.py": "164ad8a1b43671c2a47829502f9a40f4355fc8060f2f6c2506b8aec44f1c0a51",
}

_LOCK = threading.RLock()
_INSTALLED = False


def _recovery_pending(home, key):
    """Unreadable recovery state is unknown, never an empty history."""
    from tui_gateway import turn_marker

    with turn_marker._lock:
        try:
            with open(turn_marker._marker_path(home), encoding="utf-8") as stream:
                entries = json.load(stream)
        except FileNotFoundError:
            return False
    if not isinstance(entries, dict):
        raise ValueError("invalid interrupted-turn store")
    if key not in entries:
        return False
    if (
        not isinstance(entries[key], dict)
        or not isinstance(entries[key].get("prompt"), str)
        or not entries[key]["prompt"].strip()
    ):
        raise ValueError("invalid interrupted-turn record")
    return True


def _verify_hashes() -> None:
    import tui_gateway

    root = os.path.dirname(os.path.abspath(tui_gateway.__file__))
    for rel, want in _HASHES.items():
        path = os.path.join(root, os.path.basename(rel))
        with open(path, "rb") as f:
            got = hashlib.sha256(f.read()).hexdigest()
        if got != want:
            raise RuntimeError(
                f"ods_talk_resume: hash mismatch for {rel}: {got} != {want}"
            )


def install(server) -> None:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return
        _verify_hashes()

        from pydantic import StrictBool
        from tui_gateway.contracts import base as cbase
        from tui_gateway.contracts import registry as ccat

        # --- 1. Extend Pydantic params/result classes ---
        old_contract = ccat.METHODS["session.resume"]
        old_params = old_contract.params
        old_result = old_contract.result

        class _NewParams(old_params):
            ods_talk_manual_resume: StrictBool = False

        class _NewResult(old_result):
            ods_recovery_pending: StrictBool | None = None

        _NewParams.__name__ = old_params.__name__
        _NewResult.__name__ = old_result.__name__

        new_contract = ccat.MethodContract(
            name=old_contract.name,
            params=_NewParams,
            result=_NewResult,
            doc=old_contract.doc,
        )

        # --- 2. Wrap session.resume handler ---
        orig_resume = server._methods["session.resume"]

        def _wrapped_resume(rid, params):
            if not isinstance(params, dict):
                return server._err(rid, 4000, "session.resume params must be an object")
            if (
                "ods_talk_manual_resume" in params
                and type(params["ods_talk_manual_resume"]) is not bool
            ):
                return server._err(
                    rid, 4000, "ods_talk_manual_resume must be a boolean"
                )
            manual = params.get("ods_talk_manual_resume") is True
            if manual:
                for k in ("lazy", "defer_history", "eager_build", "omit_messages"):
                    if params.get(k, False) is not False:
                        return server._err(
                            rid,
                            4000,
                            f"ods_talk_manual_resume requires full cold restore; {k} must be false",
                        )
            result = orig_resume(rid, params)
            if not manual:
                return result
            if not isinstance(result, dict) or "error" in result:
                return result
            payload = result.get("result")
            if not isinstance(payload, dict):
                return result
            sid = payload.get("session_id")
            if not sid:
                return server._err(
                    rid, 5000, "ods_talk_resume: resume returned no session_id"
                )
            with server._sessions_lock:
                record = server._sessions.get(sid)
            if record is None:
                return server._err(
                    rid, 5000, f"ods_talk_resume: no live record for {sid}"
                )
            home = server._session_home(record)
            key = record.get("session_key")
            if not isinstance(key, str) or not key:
                return server._err(
                    rid,
                    5000,
                    "ODS Talk cannot verify the restored conversation identity",
                )
            try:
                # A newly created draft has no database row until its first
                # prompt. Upstream's live-unpersisted resume omits activity
                # fields. Obtain them from its authoritative live snapshot;
                # never interpret omitted fields as proof that work is idle.
                live = server._live_session_payload(sid, record, omit_messages=True)
                for name in (
                    "running",
                    "status",
                    "inflight",
                    "queued",
                    "pending_approval",
                    "open_requests",
                    "pending_connection",
                ):
                    payload[name] = live.get(name)
                payload["ods_recovery_pending"] = _recovery_pending(home, key)
            except (OSError, ValueError, TypeError):
                return server._err(
                    rid,
                    5000,
                    "ODS Talk cannot verify interrupted-turn state; no prompt was submitted",
                )
            return result

        # --- 3. Wrap _Resume.record and _maybe_schedule_auto_continue ---
        orig_record = server._Resume.record
        orig_sched = server._maybe_schedule_auto_continue

        def _wrapped_record(self, source, cwd, history, overrides=None, **extra):
            rec = orig_record(self, source, cwd, history, overrides, **extra)
            if self.params.get("ods_talk_manual_resume") is True:
                rec["_ods_talk_manual_resume"] = True
            return rec

        def _wrapped_sched(sid, session, session_key):
            if session.get("_ods_talk_manual_resume") is True:
                return None
            return orig_sched(sid, session, session_key)

        # --- 4. Declare ods.talk.capabilities ---
        class _CapsParams(cbase.Params):
            pass

        class _CapsResult(cbase.Result):
            manual_resume: bool
            version: str

        caps_contract = ccat.MethodContract(
            name="ods.talk.capabilities",
            params=_CapsParams,
            result=_CapsResult,
            doc="ODS talk adapter capabilities.",
        )

        def _caps_handler(rid, params):
            return server._ok(rid, {"manual_resume": True, "version": "1"})

        if (
            "ods.talk.capabilities" in server._methods
            or "ods.talk.capabilities" in ccat.METHODS
        ):
            raise RuntimeError("ODS Talk capability name already registered")

        # --- Stage all mutations, then commit atomically ---
        try:
            ccat.METHODS["session.resume"] = new_contract
            server._methods["session.resume"] = _wrapped_resume
            server._Resume.record = _wrapped_record
            server._maybe_schedule_auto_continue = _wrapped_sched
            ccat.METHODS["ods.talk.capabilities"] = caps_contract
            server.register_method("ods.talk.capabilities", _caps_handler)
        except Exception:
            # Roll back any partial assignment
            ccat.METHODS["session.resume"] = old_contract
            server._methods["session.resume"] = orig_resume
            server._Resume.record = orig_record
            server._maybe_schedule_auto_continue = orig_sched
            ccat.METHODS.pop("ods.talk.capabilities", None)
            server._methods.pop("ods.talk.capabilities", None)
            raise

        _INSTALLED = True
