from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class SellerConfig(BaseModel):
    network: str = "eip155:84532"
    asset: str = "USDC"
    recipient: str
    facilitatorUrl: HttpUrl = Field(default="https://x402.org/facilitator")


class PolicyConfig(BaseModel):
    mode: Literal["allowlist"] = "allowlist"
    unprotectedByDefault: bool = True
    devBypass: bool = False


class AuditConfig(BaseModel):
    logPayments: bool = True
    logUnpaidAttempts: bool = True


class PriceConfig(BaseModel):
    amount: str
    currency: str = "USDC"

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, value: str) -> str:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError("price amount must be numeric") from exc
        if parsed <= 0:
            raise ValueError("price amount must be positive")
        return value


class RuleMetadata(BaseModel):
    title: str | None = None
    description: str | None = None
    mimeType: str = "application/json"


class RouteRule(BaseModel):
    name: str
    kind: Literal["http_route"] = "http_route"
    path: str
    methods: list[str] = Field(default_factory=lambda: ["POST"])
    upstream: HttpUrl
    price: PriceConfig
    metadata: RuleMetadata = Field(default_factory=RuleMetadata)

    @field_validator("path")
    @classmethod
    def path_must_be_absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("route path must start with /")
        return value

    @field_validator("methods")
    @classmethod
    def normalize_methods(cls, value: list[str]) -> list[str]:
        methods = [method.upper() for method in value]
        if not methods:
            raise ValueError("at least one method is required")
        return methods


class GatewayConfig(BaseModel):
    enabled: bool = True
    seller: SellerConfig
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    rules: list[RouteRule] = Field(default_factory=list)

    @field_validator("rules")
    @classmethod
    def require_unique_routes(cls, value: list[RouteRule]) -> list[RouteRule]:
        seen: set[tuple[str, str]] = set()
        for rule in value:
            for method in rule.methods:
                key = (method, rule.path)
                if key in seen:
                    raise ValueError(f"duplicate route rule for {method} {rule.path}")
                seen.add(key)
        return value


def load_config(path: str | Path) -> GatewayConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return GatewayConfig.model_validate(payload)
