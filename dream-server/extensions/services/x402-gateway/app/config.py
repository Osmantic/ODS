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


class FacilitatorAuthConfig(BaseModel):
    type: Literal["none", "cdp_api_key"] = "none"
    apiKeyIdEnv: str = "CDP_API_KEY_ID"
    apiKeySecretEnv: str = "CDP_API_KEY_SECRET"


class FacilitatorConfig(BaseModel):
    url: HttpUrl = Field(default="https://x402.org/facilitator")
    provider: Literal["x402.org", "cdp", "custom"] = "x402.org"
    auth: FacilitatorAuthConfig = Field(default_factory=FacilitatorAuthConfig)


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
    mode: Literal["per_request"] = "per_request"

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


class VendorOperatorConfig(BaseModel):
    displayName: str = "Dream Server operator"


class VendorConfig(BaseModel):
    id: str = "dream-server-local-node"
    name: str = "Dream Server local node"
    description: str = "Local AI capability vendor node"
    protocolVersion: str = "dream-server-v1"
    version: str = "0.1.0"
    operator: VendorOperatorConfig = Field(default_factory=VendorOperatorConfig)


class TimeoutLimits(BaseModel):
    defaultSeconds: int = 60
    maxSeconds: int = 300


class RateLimits(BaseModel):
    requestsPerMinute: int = 10
    concurrentRequests: int = 2


class LimitsConfig(BaseModel):
    maxPromptChars: int = 50000
    maxContextItems: int = 20
    maxFileBytes: int = 200000
    maxOutputTokens: int = 4096
    supportsStreaming: bool = True
    supportsFiles: bool = False
    timeouts: TimeoutLimits = Field(default_factory=TimeoutLimits)
    rateLimits: RateLimits = Field(default_factory=RateLimits)


class CapabilityLimits(BaseModel):
    maxPromptChars: int = 50000
    maxOutputTokens: int = 4096


def default_capability_input_schema() -> dict[str, object]:
    return {
        "prompt": "string",
        "context": "object",
        "stream": "boolean",
        "max_tokens": "optional number",
    }


def default_capability_output_schema() -> dict[str, object]:
    return {
        "output": "string",
        "request_id": "string",
        "usage": {
            "input_tokens": "number",
            "output_tokens": "number",
        },
    }


class CapabilityConfig(BaseModel):
    id: str
    description: str
    path: str
    streaming: bool = True
    riskLevel: Literal["low", "medium", "high"] = "medium"
    pricing: PriceConfig
    limits: CapabilityLimits = Field(default_factory=CapabilityLimits)
    inputSchema: dict[str, object] = Field(default_factory=default_capability_input_schema)
    outputSchema: dict[str, object] = Field(default_factory=default_capability_output_schema)
    examples: list[dict[str, object]] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def capability_path_must_be_absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("capability path must start with /")
        return value


class GatewayConfig(BaseModel):
    enabled: bool = True
    seller: SellerConfig
    facilitator: FacilitatorConfig | None = None
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    vendor: VendorConfig = Field(default_factory=VendorConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    capabilities: list[CapabilityConfig] = Field(default_factory=list)
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

    @field_validator("capabilities")
    @classmethod
    def require_unique_capabilities(cls, value: list[CapabilityConfig]) -> list[CapabilityConfig]:
        seen: set[str] = set()
        for capability in value:
            if capability.id in seen:
                raise ValueError(f"duplicate capability id {capability.id}")
            seen.add(capability.id)
        return value

    def facilitator_url(self) -> str:
        if self.facilitator:
            return str(self.facilitator.url)
        return str(self.seller.facilitatorUrl)


def load_config(path: str | Path) -> GatewayConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return GatewayConfig.model_validate(payload)
