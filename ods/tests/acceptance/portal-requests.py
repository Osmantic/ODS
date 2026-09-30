#!/usr/bin/env python3
"""Private request helper for the disposable Portal acceptance runner."""

import json
import re
import sys
import urllib.error
import urllib.request


def read_key(env_path):
    with open(env_path, encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("DASHBOARD_API_KEY="):
                value = line.partition("=")[2].strip()
                if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                    value = value[1:-1]
                if not value or any(ch in value for ch in "\r\n"):
                    raise ValueError("installed Dashboard API key is invalid")
                return value
    raise ValueError("installed Dashboard API key is missing")


def request(key, method, path, payload=None, timeout=30):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": "Bearer " + key}
    if body is not None:
        headers["Content-Type"] = "application/json"
    call = urllib.request.Request(
        "http://127.0.0.1:3002" + path, data=body, headers=headers, method=method
    )
    return urllib.request.urlopen(call, timeout=timeout)


def get_json(key, method, path, payload=None, timeout=30):
    with request(key, method, path, payload, timeout) as response:
        return json.load(response)


def main():
    action, env_path = sys.argv[1:3]
    key = read_key(env_path)
    if action == "status":
        result = get_json(key, "GET", "/api/pixel/status", timeout=30)
        assert result.get("available") is True and result.get("model") == "portal/default", result
        print("PASS: installed Portal API reports the agent ready")
    elif action == "selection-off":
        result = get_json(key, "GET", "/api/webui/selection")
        assert result == {"enabled": False, "supported": True}, result
        print("PASS: installed WebUI selection is off")
    elif action == "selection-on":
        result = get_json(key, "GET", "/api/webui/selection")
        assert result == {"enabled": True, "supported": True}, result
        print("PASS: installed WebUI selection is on")
    elif action == "add-webui":
        result = get_json(key, "POST", "/api/webui/selection", {"enabled": True}, timeout=900)
        assert result.get("enabled") is True and result.get("action") == "enabled", result
        print("PASS: Dashboard API added Open WebUI")
    elif action == "expect-add-failure":
        try:
            get_json(key, "POST", "/api/webui/selection", {"enabled": True}, timeout=900)
        except urllib.error.HTTPError as error:
            assert error.code in {502, 503}, error.code
            print("PASS: failed Open WebUI start was reported as failure")
        else:
            raise AssertionError("failed Open WebUI start was reported as success")
    elif action == "chat":
        body = {"chat_id": "odsacceptance", "request_id": "odsacceptancefirst",
                "messages": [{"role": "user", "content": "Reply OK."}]}
        answer = []
        done = False
        with request(key, "POST", "/api/pixel/chat/stream", body, timeout=1200) as response:
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip()
                if line == "data: [DONE]":
                    done = True
                    break
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                if "error" in event:
                    error = event["error"]
                    summary = {"eventKeys": sorted(event)}
                    if isinstance(error, dict):
                        for field in ("code", "type", "status"):
                            value = error.get(field)
                            if isinstance(value, int) or (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", value)):
                                summary[field] = value
                        summary["errorKeys"] = sorted(error)
                    else:
                        summary["errorType"] = type(error).__name__
                    print("Portal error event summary: " + json.dumps(summary), file=sys.stderr)
                    raise AssertionError("Portal returned an error event")
                for choice in event.get("choices", []):
                    chunk = choice.get("delta", {}).get("content")
                    if isinstance(chunk, str):
                        answer.append(chunk)
        assert done and "OK" in "".join(answer), "Portal chat did not complete with the mock answer"
        print("PASS: Portal streamed a real mock-upstream chat completion")
    elif action == "result":
        result = get_json(key, "POST", "/api/pixel/chat/result",
                          {"chat_id": "odsacceptance", "request_id": "odsacceptancefirst"})
        assert result.get("state") == "complete" and "OK" in result.get("events", ""), result.get("state")
        print("PASS: completed Portal result persisted")
    else:
        raise ValueError("unknown acceptance action")


if __name__ == "__main__":
    main()
