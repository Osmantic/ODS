"""Run the model-profile battery against one llama-server (lab runs and CI).

    PYTHONPATH=ods/bin python3 -m model_profile --url http://127.0.0.1:8080

Prints the battery result (facts, probes, summary) as JSON: the record the
host agent stores per model, without its installation key. Nothing is
written anywhere. A key, when the server needs one, is read from the
environment variable that ``--api-key-env`` names, never from argv.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
from urllib import error as urllib_error
from urllib import request as urllib_request

from . import probes

RESPONSE_LIMIT = 2 * 1024 * 1024  # the host agent's bound for one probe answer


class _RefuseRedirects(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_exchange(base_url: str, api_key: str | None = None) -> probes.Exchange:
    """``exchange(path, payload, timeout) -> (status, text)``, error statuses included."""
    opener = urllib_request.build_opener(urllib_request.ProxyHandler({}), _RefuseRedirects())
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def exchange(path: str, payload: dict | None, timeout: float) -> tuple[int, str]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib_request.Request(base_url.rstrip("/") + path, data=data, headers=headers)
        try:
            try:
                with opener.open(request, timeout=timeout) as response:
                    status, body = response.status, response.read(RESPONSE_LIMIT + 1)
            except urllib_error.HTTPError as exc:
                if 300 <= exc.code < 400:
                    raise OSError(f"{path} redirected; refusing") from exc
                status, body = exc.code, exc.read(RESPONSE_LIMIT + 1)
        except http.client.HTTPException as exc:
            raise OSError(f"{path} answered incompletely: {exc}") from exc
        if len(body) > RESPONSE_LIMIT:
            raise OSError(f"{path} answer exceeds {RESPONSE_LIMIT} bytes")
        return status, body.decode("utf-8", errors="replace")

    return exchange


def profile(exchange: probes.Exchange, *, budget_seconds: float) -> dict:
    status, text = exchange("/props", None, 15)
    if status != 200:
        raise OSError(f"/props answered HTTP {status}")
    props = json.loads(text)
    if not isinstance(props, dict):
        raise ValueError("/props is not a JSON object")
    return probes.run_battery(exchange, props, budget_seconds=budget_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m model_profile", description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="llama-server origin, for example http://127.0.0.1:8080")
    parser.add_argument("--api-key-env", help="name of the environment variable that holds the server's API key")
    parser.add_argument("--budget", type=float, default=probes.BUDGET_SECONDS,
                        help="total seconds for the battery (default %(default)s, the activation budget)")
    args = parser.parse_args(argv)
    api_key = os.environ.get(args.api_key_env, "") if args.api_key_env else None
    if args.api_key_env and not api_key:
        parser.error(f"{args.api_key_env} is not set")
    result = profile(http_exchange(args.url, api_key), budget_seconds=args.budget)
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
