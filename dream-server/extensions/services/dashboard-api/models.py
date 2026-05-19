"""Pydantic response models for Dream Server Dashboard API."""

from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field

from config import GPU_BACKEND


class GPUInfo(BaseModel):
    name: str
    memory_used_mb: int
    memory_total_mb: int
    memory_percent: float
    utilization_percent: int
    temperature_c: int
    power_w: Optional[float] = None
    memory_type: str = "discrete"
    gpu_backend: str = GPU_BACKEND


class ServiceStatus(BaseModel):
    id: str
    name: str
    port: int
    external_port: int
    status: str  # "healthy", "unhealthy", "unknown", "down", "not_deployed"
    response_time_ms: Optional[float] = None


class DiskUsage(BaseModel):
    path: str
    used_gb: float
    total_gb: float
    percent: float


class ModelInfo(BaseModel):
    name: str
    size_gb: float
    context_length: int
    quantization: Optional[str] = None


class BootstrapStatus(BaseModel):
    active: bool
    model_name: Optional[str] = None
    percent: Optional[float] = None
    downloaded_gb: Optional[float] = None
    total_gb: Optional[float] = None
    speed_mbps: Optional[float] = None
    eta_seconds: Optional[int] = None


class FullStatus(BaseModel):
    timestamp: str
    gpu: Optional[GPUInfo] = None
    services: list[ServiceStatus]
    disk: DiskUsage
    model: Optional[ModelInfo] = None
    bootstrap: BootstrapStatus
    uptime_seconds: int


PortNumber = Annotated[int, Field(ge=1, le=65535)]


class PortCheckRequest(BaseModel):
    ports: list[PortNumber]


class PortConflict(BaseModel):
    port: int
    service: str
    in_use: bool


class PersonaRequest(BaseModel):
    persona: str


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=100000)
    system: Optional[str] = Field(None, max_length=10000)


class VersionInfo(BaseModel):
    current: str
    latest: Optional[str] = None
    update_available: bool = False
    changelog_url: Optional[str] = None
    checked_at: Optional[str] = None


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
    power_w: Optional[float] = None
    assigned_services: list[str] = []


class MultiGPUStatus(BaseModel):
    gpu_count: int
    backend: str  # "nvidia", "amd", "apple"
    gpus: list[IndividualGPU]
    topology: Optional[dict] = None
    assignment: Optional[dict] = None
    split_mode: Optional[str] = None
    tensor_split: Optional[str] = None
    aggregate: GPUInfo


class ModelLibraryEntry(BaseModel):
    id: str
    name: str
    gguf: Optional[str] = None
    ggufParts: Optional[list[dict[str, Any]]] = None
    downloadUrl: Optional[str] = None
    downloadSha256: Optional[str] = None
    llmModelName: Optional[str] = None
    size: str
    sizeGb: float
    vramRequired: float
    estimatedRequired: Optional[float] = None
    contextLength: int
    specialty: str
    description: str
    tokensPerSec: Optional[float] = None
    tokensPerSecEstimate: Optional[float] = None
    quantization: Optional[str] = None
    architecture: Optional[str] = None
    activeParamsB: Optional[float] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str  # "loaded", "downloaded", "available"
    recommended: bool = False
    configured: bool = False
    recommendation: Optional[dict[str, Any]] = None
    fitsVram: bool
    fitsCurrentVram: bool
    performance: Optional[dict[str, Any]] = None
    performanceLabel: Optional[str] = None


class ModelLibraryGpu(BaseModel):
    vramTotal: float
    vramUsed: float
    vramFree: float


class ModelLibraryResponse(BaseModel):
    models: list[ModelLibraryEntry]
    gpu: Optional[ModelLibraryGpu] = None
    currentModel: Optional[str] = None
    loadedModel: Optional[str] = None
    configuredModel: Optional[str] = None
    recommendationPolicy: Optional[str] = None
    recommendationAlternatives: list[dict[str, Any]] = Field(default_factory=list)
