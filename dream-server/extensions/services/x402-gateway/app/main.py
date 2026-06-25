from __future__ import annotations

import os
from collections.abc import Callable

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from x402.http.middleware.fastapi import PaymentMiddlewareASGI

from .audit import AuditLog
from .config import CapabilityConfig, GatewayConfig, RouteRule, load_config
from .gateway import proxy_request
from .payments import build_resource_server, build_x402_routes
from .policy import RoutePolicy


CONFIG_PATH = os.environ.get("X402_CONFIG_PATH", "/config/config.json")
AUDIT_LOG_PATH = os.environ.get("X402_AUDIT_LOG", "/data/audit.jsonl")
PORT = int(os.environ.get("X402_GATEWAY_PORT_INTERNAL", "4020"))


def create_app(config: GatewayConfig | None = None) -> FastAPI:
    loaded = config or load_config(CONFIG_PATH)
    app = FastAPI(title="Dream Server x402 Gateway")
    app.state.config = loaded
    app.state.policy = RoutePolicy(loaded)
    app.state.audit = AuditLog(AUDIT_LOG_PATH)
    app.state.http = httpx.AsyncClient(timeout=60.0, follow_redirects=False)

    @app.on_event("shutdown")
    async def shutdown_http_client() -> None:
        await app.state.http.aclose()

    @app.get("/health")
    async def legacy_health() -> dict[str, object]:
        return _health_payload(loaded)

    @app.get("/v1/health")
    async def health() -> dict[str, object]:
        return _health_payload(loaded)

    @app.get("/v1/health/ready")
    async def ready() -> dict[str, object]:
        return _readiness_payload(loaded)

    @app.get("/v1/vendor")
    async def vendor() -> dict[str, object]:
        return loaded.vendor.model_dump(mode="json")

    @app.get("/v1/limits")
    async def limits() -> dict[str, object]:
        return loaded.limits.model_dump(mode="json")

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, object]:
        return {
            "provider": loaded.vendor.model_dump(mode="json"),
            "capabilities": [
                capability.model_dump(mode="json")
                for capability in _capabilities(loaded)
            ],
        }

    if loaded.enabled and not loaded.policy.devBypass:
        app.add_middleware(
            PaymentMiddlewareASGI,
            routes=build_x402_routes(loaded),
            server=build_resource_server(loaded),
        )

    for rule in loaded.rules:
        handler = _handler_for_rule(rule.path)
        for method in rule.methods:
            app.add_api_route(
                rule.path,
                handler,
                methods=[method],
                name=f"x402_{method.lower()}_{rule.path.strip('/').replace('/', '_')}",
            )

    return app


def _health_payload(config: GatewayConfig) -> dict[str, object]:
    return {
        "status": "ok",
        "service": "dream-server-x402-gateway",
        "version": config.vendor.version,
        "protocolVersion": config.vendor.protocolVersion,
        "enabled": config.enabled,
        "rules": len(config.rules),
    }


def _readiness_payload(config: GatewayConfig) -> dict[str, object]:
    checks = {
        "api": "ok",
        "capability_registry": "ok" if _capabilities(config) else "down",
        "payment_gateway": "ok" if config.enabled else "disabled",
        "payment_rules": "ok" if config.rules else "down",
        "usage_metering": "ok" if config.audit.logPayments else "disabled",
    }
    status = "ok" if all(value in {"ok", "disabled"} for value in checks.values()) else "degraded"
    return {"status": status, "checks": checks}


def _capabilities(config: GatewayConfig) -> list[CapabilityConfig]:
    if config.capabilities:
        return config.capabilities
    return [_capability_from_rule(rule) for rule in config.rules]


def _capability_from_rule(rule: RouteRule) -> CapabilityConfig:
    capability_id = rule.name.lower().replace(" ", "_").replace("-", "_")
    return CapabilityConfig(
        id=capability_id,
        description=rule.metadata.description or rule.name,
        path=rule.path,
        streaming=True,
        riskLevel="medium",
        pricing=rule.price,
    )


def _handler_for_rule(path: str) -> Callable[[Request], object]:
    async def handler(request: Request) -> Response:
        policy: RoutePolicy = request.app.state.policy
        rule = policy.match(request.method, path)
        if not rule:
            raise HTTPException(status_code=404, detail="route_not_configured")
        return await proxy_request(
            request,
            rule,
            request.app.state.http,
            request.app.state.audit,
        )

    return handler


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
