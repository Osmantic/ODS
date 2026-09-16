"""Pydantic response models for ODS Dashboard API."""

from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field

from config import GPU_BACKEND
from context_policy import HERMES_MIN_CONTEXT, HERMES_TARGET_CONTEXT


class GPUInfo(BaseModel):
    name: str
    memory_used_mb: int
    memory_total_mb: int
    memory_percent: float
    utilization_percent: int
    temperature_c: int
    power_w: float | None = None
    memory_type: str = "discrete"
    gpu_backend: str = GPU_BACKEND
    gpu_count: int = 1
    memory_usage_available: bool = True
    utilization_available: bool = True
    temperature_available: bool = True


class ServiceStatus(BaseModel):
    id: str
    name: str
    port: int
    external_port: int
    status: str  # "healthy", "unhealthy", "unknown", "degraded", "down", "not_deployed"
    response_time_ms: float | None = None


class NodeCapabilities(BaseModel):
    ods_version: str
    gpu: GPUInfo | None = None
    loaded_model: str | None = None
    services: list[ServiceStatus] = []
    service_count: int = 0
    running_service_count: int = 0


class DiskUsage(BaseModel):
    path: str
    used_gb: float
    total_gb: float
    percent: float


class ModelInfo(BaseModel):
    name: str
    size_gb: float
    context_length: int
    quantization: str | None = None


class BootstrapStatus(BaseModel):
    active: bool
    model_name: str | None = None
    percent: float | None = None
    downloaded_gb: float | None = None
    total_gb: float | None = None
    speed_mbps: float | None = None
    eta_seconds: int | None = None


class FullStatus(BaseModel):
    timestamp: str
    gpu: GPUInfo | None = None
    services: list[ServiceStatus]
    disk: DiskUsage
    model: ModelInfo | None = None
    bootstrap: BootstrapStatus
    uptime_seconds: int


PortNumber = Annotated[int, Field(ge=1, le=65535)]


class PortCheckRequest(BaseModel):
    # preflight_ports binds a socket per entry synchronously on the event loop,
    # so cap the list. A real install exposes a couple dozen service ports;
    # 128 leaves ample headroom while preventing bind-probe amplification.
    ports: Annotated[list[PortNumber], Field(max_length=128)]


class PortConflict(BaseModel):
    port: int
    service: str
    in_use: bool


class PersonaRequest(BaseModel):
    persona: str


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=100000)
    system: str | None = Field(None, max_length=10000)


class VersionInfo(BaseModel):
    current: str
    latest: str | None = None
    update_available: bool = False
    changelog_url: str | None = None
    checked_at: str | None = None


class UpdateAction(BaseModel):
    action: str  # "check", "backup", "update"


class PrivacyShieldStatus(BaseModel):
    enabled: bool
    container_running: bool
    port: int
    target_api: str
    pii_cache_enabled: bool
    message: str


class PrivacyShieldToggle(BaseModel):
    enable: bool


class IndividualGPU(BaseModel):
    index: int
    uuid: str
    name: str
    memory_used_mb: int
    memory_total_mb: int
    memory_percent: float
    utilization_percent: int
    temperature_c: int
    power_w: float | None = None
    memory_type: str = "discrete"
    assigned_services: list[str] = []
    memory_usage_available: bool = True
    utilization_available: bool = True
    temperature_available: bool = True


class MultiGPUStatus(BaseModel):
    gpu_count: int
    backend: str  # "nvidia", "amd", "apple"
    gpus: list[IndividualGPU]
    topology: dict | None = None
    assignment: dict | None = None
    split_mode: str | None = None
    tensor_split: str | None = None
    aggregate: GPUInfo


class AmdRuntimeStatus(BaseModel):
    available: bool
    reason: str | None = None
    runtime: str = "none"
    location: str = "none"
    runtimeMode: str = "unknown"
    managedByODS: bool = False
    selectedBackend: str = "none"
    supportedBackends: list[str] = Field(default_factory=list)
    defaultBackend: str = "none"
    apiBase: str | None = None
    healthUrl: str | None = None
    health: str | None = None
    version: str = "unknown"
    loadedModel: str | None = None
    modelCount: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ModelLibraryEntry(BaseModel):
    id: str
    name: str
    gguf: str | None = None
    ggufParts: list[dict[str, Any]] | None = None
    downloadUrl: str | None = None
    downloadSha256: str | None = None
    llmModelName: str | None = None
    size: str
    sizeGb: float
    vramRequired: float
    estimatedRequired: float | None = None
    contextLength: int
    maxContextLength: int | None = None
    contextOptions: list[dict[str, Any]] = Field(default_factory=list)
    specialty: str
    description: str
    tokensPerSec: float | None = None
    tokensPerSecEstimate: float | None = None
    quantization: str | None = None
    architecture: str | None = None
    activeParamsB: float | None = None
    publisher: dict[str, str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    appCompatibility: dict[str, Any] = Field(default_factory=dict)
    status: str  # "loaded", "downloaded", "available"
    modelOperation: dict[str, Any] | None = None
    recommended: bool = False
    configured: bool = False
    recommendation: dict[str, Any] | None = None
    fitsVram: bool
    fitsCurrentVram: bool
    performance: dict[str, Any] | None = None
    performanceLabel: str | None = None


class ModelLibraryGpu(BaseModel):
    vramTotal: float
    vramUsed: float
    vramFree: float


class ModelLibraryResponse(BaseModel):
    models: list[ModelLibraryEntry]
    gpu: ModelLibraryGpu | None = None
    currentModel: str | None = None
    activationReadyModel: str | None = None
    loadedModel: str | None = None
    configuredModel: str | None = None
    hermesMinimumContext: int = HERMES_MIN_CONTEXT
    hermesTargetContext: int = HERMES_TARGET_CONTEXT
    recommendationPolicy: str | None = None
    recommendationAlternatives: list[dict[str, Any]] = Field(default_factory=list)
    modelLifecycle: dict[str, Any] | None = None
    odsMode: str = "unknown"
    configuredMode: str = "unknown"
    llmBackend: str = "unknown"
