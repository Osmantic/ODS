"""Strict public projection; errors are mapped to content-free responses."""

from __future__ import annotations
from typing import Optional, List, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import re
import unicodedata
from urllib.parse import urlsplit

ID_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"

class ProviderPublic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=ID_PATTERN, max_length=64)
    label: str = Field(min_length=1, max_length=256)
    kind: Literal["local", "ods-peer", "cloud"]
    baseUrl: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    contextTokens: int = Field(ge=256, le=10_000_000)
    maxOutputTokens: int = Field(ge=1, le=10_000_000)
    supportsTools: bool
    supportsVision: bool
    reasoning: bool
    enabled: bool
    hasCredential: bool

    @field_validator("baseUrl")
    @classmethod
    def credential_free_url(cls, value: str) -> str:
        if (value != value.strip() or any(c in value for c in "\\@?#")
                or any(c.isspace() or unicodedata.category(c).startswith("C") for c in value)):
            raise ValueError("invalid public provider URL")
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or not parsed.path.endswith("/v1")
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError("invalid public provider URL")
        return value

    @field_validator("label", "model")
    @classmethod
    def strip_nonempty(cls, v: str) -> str:
        if not v.strip() or any(unicodedata.category(c).startswith("C") for c in v):
            raise ValueError("invalid text")
        return v.strip()

    @field_validator("id")
    @classmethod
    def valid_id(cls, v: str) -> str:
        if not re.fullmatch(ID_PATTERN, v):
            raise ValueError("invalid id format")
        return v

    @model_validator(mode="after")
    def check_cloud_credential(self) -> ProviderPublic:
        if self.kind == "cloud" and self.enabled and not self.hasCredential:
            raise ValueError("enabled cloud provider needs a credential reference")
        return self

class RolesPublic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    leader: Optional[str]
    backups: List[str] = Field(max_length=8)
    advisor: Optional[str]
    handoff: Optional[str]

    @field_validator("leader", "advisor", "handoff")
    @classmethod
    def valid_role_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.fullmatch(ID_PATTERN, v):
            raise ValueError("invalid role id")
        return v

    @field_validator("backups")
    @classmethod
    def valid_backup_ids(cls, v: List[str]) -> List[str]:
        for b in v:
            if not re.fullmatch(ID_PATTERN, b):
                raise ValueError("invalid backup id")
        return v

class PolicyPublic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    allowCloud: bool
    maxAttempts: int = Field(ge=1, le=9)
    deadlineSeconds: int = Field(ge=1, le=3600)

class ConfigurationPublic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schemaVersion: int = Field(ge=1, le=1)
    revision: int = Field(ge=0, le=2**53 - 1)
    enabled: bool
    providers: List[ProviderPublic] = Field(max_length=32)
    roles: RolesPublic
    policy: PolicyPublic

    @model_validator(mode="after")
    def check_schema_version(self) -> ConfigurationPublic:
        if self.schemaVersion != 1:
            raise ValueError("schemaVersion must be exactly 1")
        return self

    @model_validator(mode="after")
    def check_unique_provider_ids(self) -> ConfigurationPublic:
        ids = [p.id for p in self.providers]
        if len(ids) != len(set(ids)):
            raise ValueError("provider ids must be unique")
        return self

    @model_validator(mode="after")
    def check_unique_backups(self) -> ConfigurationPublic:
        if len(self.roles.backups) != len(set(self.roles.backups)):
            raise ValueError("backup ids must be unique")
        return self

    @model_validator(mode="after")
    def check_backups_not_leader(self) -> ConfigurationPublic:
        if self.roles.leader and self.roles.leader in self.roles.backups:
            raise ValueError("leader cannot be in backups")
        return self

    @model_validator(mode="after")
    def check_roles_exist(self) -> ConfigurationPublic:
        provider_ids = {p.id for p in self.providers}
        for role_id in [self.roles.leader, self.roles.advisor, self.roles.handoff] + self.roles.backups:
            if role_id is not None and role_id not in provider_ids:
                raise ValueError("unknown role reference")
        return self

    @model_validator(mode="after")
    def check_enabled_leader(self) -> ConfigurationPublic:
        if self.enabled and self.roles.leader is None:
            raise ValueError("leader must be set when enabled")
        return self

    @model_validator(mode="after")
    def check_roles_enabled(self) -> ConfigurationPublic:
        if not self.enabled:
            return self
        provider_map = {p.id: p for p in self.providers}
        for role_id in [self.roles.leader, self.roles.advisor, self.roles.handoff] + self.roles.backups:
            if role_id is not None:
                p = provider_map.get(role_id)
                if p and not p.enabled:
                    raise ValueError("disabled provider reference")
        return self

    @model_validator(mode="after")
    def check_cloud_policy(self) -> ConfigurationPublic:
        if not self.enabled:
            return self
        provider_map = {p.id: p for p in self.providers}
        for role_id in [self.roles.leader, self.roles.advisor, self.roles.handoff] + self.roles.backups:
            if role_id is not None:
                p = provider_map.get(role_id)
                if p and p.kind == "cloud" and not self.policy.allowCloud:
                    raise ValueError("cloud provider used but allowCloud is false")
        return self

    @model_validator(mode="after")
    def check_output_tokens(self) -> ConfigurationPublic:
        for p in self.providers:
            if p.maxOutputTokens > p.contextTokens:
                raise ValueError("maxOutputTokens cannot exceed contextTokens")
        return self

def normalize_public(value: dict) -> dict:
    return ConfigurationPublic.model_validate(value).model_dump()
