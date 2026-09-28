"""Bounded, nonsecret projection of host-authoritative active runtime metadata."""

import re


def active_runtime_projection(status: object) -> dict[str, object] | None:
    if not isinstance(status, dict):
        return None
    runtime = status.get("activeRuntime")
    if isinstance(runtime, dict) and runtime.get("source") == "local-switchboard":
        expected = {"source", "model", "contextLength"}
        if (
            set(runtime) == expected
            and isinstance(runtime.get("model"), str)
            and 1 <= len(runtime["model"]) <= 256
            and type(runtime.get("contextLength")) is int
            and 1 <= runtime["contextLength"] <= 10_000_000
        ):
            return {key: runtime[key] for key in expected}
        return None
    if isinstance(runtime, dict) and runtime.get("source") == "external-host":
        expected = {"source", "model"}
        if (
            expected <= set(runtime) <= expected | {"contextLength"}
            and isinstance(runtime.get("model"), str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+:-]{0,255}", runtime["model"])
            and "://" not in runtime["model"]
            and (
                "contextLength" not in runtime
                or type(runtime["contextLength"]) is int
                and 1 <= runtime["contextLength"] <= 10_000_000
            )
        ):
            return {key: runtime[key] for key in expected | {"contextLength"} if key in runtime}
        return None
    expected = {"source", "model", "contextLength", "maxTokens", "reasoning"}
    if (
        not isinstance(runtime, dict)
        or not expected <= set(runtime) <= expected | {"routeFingerprint"}
        or runtime.get("source") != "remote-provider"
        or not isinstance(runtime.get("model"), str)
        or not 1 <= len(runtime["model"]) <= 256
        or type(runtime.get("contextLength")) is not int
        or not 4096 <= runtime["contextLength"] <= 10_000_000
        or type(runtime.get("maxTokens")) is not int
        or not 1 <= runtime["maxTokens"] <= runtime["contextLength"]
        or type(runtime.get("reasoning")) is not bool
        or "routeFingerprint" in runtime and (
            not isinstance(runtime["routeFingerprint"], str)
            or re.fullmatch(r"[a-f0-9]{64}", runtime["routeFingerprint"]) is None
        )
    ):
        return None
    return {key: runtime[key] for key in expected | {"routeFingerprint"} if key in runtime}
