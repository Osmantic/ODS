"""Pydantic response models for Dream Server Dashboard API."""

from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field
from pydantic.networks import IPvAnyAddress

from config import GPU_BACKEND

# Defense-in-depth: these models serialize cluster state to the dashboard
# (never parse untrusted request bodies — join payloads are parsed by the
# setup listener with its own validators). Constraints here are belt-and-
# braces so a future refactor that feeds user-supplied data through these
# models, or a corrupted cluster.json slipping past the router's skip
# logic, fails at the model boundary instead of reaching the UI.
_ALLOWED_GPU_BACKENDS = frozenset({"cpu", "nvidia", "amd", "apple", ""})
_TENSOR_SPLIT_PATTERN = r"^[\d,./:\-\s]*$"
_WORKER_LIST_PATTERN = r"^[\d.,:\s\-]*$"


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


class ClusterGPU(BaseModel):
    name: str = Field(..., max_length=128)
    vram_mb: int = Field(..., ge=0, le=10_000_000)


class ClusterNode(BaseModel):
    ip: IPvAnyAddress
    rpc_port: int = Field(50052, ge=1, le=65535)
    gpu_backend: str = Field(..., max_length=16)
    gpus: list[ClusterGPU]
    status: str = Field(..., max_length=16)  # "online", "offline"
    ping_ms: Optional[float] = Field(None, ge=0)
    added_at: Optional[str] = Field(None, max_length=64)


class ClusterController(BaseModel):
    ip: IPvAnyAddress
    gpu_backend: str = Field(..., max_length=16)
    gpus: list[ClusterGPU]


class ClusterStatus(BaseModel):
    enabled: bool
    controller: Optional[ClusterController] = None
    nodes: list[ClusterNode] = []
    tensor_split: Optional[str] = Field(None, max_length=256, pattern=_TENSOR_SPLIT_PATTERN)
    worker_list: Optional[str] = Field(None, max_length=1024, pattern=_WORKER_LIST_PATTERN)


class FullStatus(BaseModel):
    timestamp: str
    gpu: Optional[GPUInfo] = None
    services: list[ServiceStatus]
    disk: DiskUsage
    model: Optional[ModelInfo] = None
    bootstrap: BootstrapStatus
    uptime_seconds: int
    cluster_enabled: bool = False


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


class AmdRuntimeStatus(BaseModel):
    available: bool
    reason: Optional[str] = None
    runtime: str = "none"
    location: str = "none"
    runtimeMode: str = "unknown"
    managedByDreamServer: bool = False
    selectedBackend: str = "none"
    supportedBackends: list[str] = Field(default_factory=list)
    defaultBackend: str = "none"
    apiBase: Optional[str] = None
    healthUrl: Optional[str] = None
    health: Optional[str] = None
    version: str = "unknown"
    capabilities: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
