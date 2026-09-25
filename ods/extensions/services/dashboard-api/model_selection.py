"""Curated, context-aware model selection shared by installer and dashboard.

scripts/select-model.py (Linux/macOS installers) and performance_oracle.py
(dashboard recommendation) both rank catalog entries through
:func:`rank_catalog_models`, so the installer and the dashboard cannot pick
different models for the same hardware. installers/windows/lib/tier-map.ps1
mirrors this module; tests/test-windows-catalog-selector.ps1 checks parity.

Ranking (lexicographic, highest first):

1. family match (gemma profile only: Gemma entries ahead of the Qwen 2B
   fallback);
2. the chosen context meets ``min_context`` (the 64K Hermes floor);
3. the entry's curated ``selection`` priority for this memory class, plus
   fleet evidence (+5 for a verified Pixel verdict, -5 for a negative one;
   expiry is not read, so selection stays deterministic and offline);
4. context credit ``min(ctx, 256K) / 64K``, minus a quarter when the pick
   leaves under 5% of capacity free;
5. weight size, as a final tie-breaker only.

File size never outranks a curated priority.

Every fit decision (ranking, :func:`plan_model_context` for a dashboard
switch, :func:`check_fit` for the installers' Hermes re-check) goes through
one gate, :func:`candidate_fits`, over one estimator,
model_memory.estimate_model_memory. The discrete-GPU residency check of the
GPU-residency change (model_memory.resident_configuration: llama.cpp's own
device projection against total VRAM minus the platform reserve, the fit
target and other processes) plugs in at that gate for discrete GPUs; unified
memory and CPU keep the class rules here.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from model_memory import (
    LEGACY_FIT_TOLERANCE_GIB,
    MemoryEstimate,
    authored_profile_estimate,
    context_candidates,
    estimate_for_runtime,
    estimate_model_memory,
    fit_margin_gib,
    memory_fits,
)


POLICY = "context-aware-curated-fit-v2"
DEFAULT_SELECTION_PRIORITY = 10
EVIDENCE_WEIGHT = 5
HEADROOM_RATIO = 0.95
HEADROOM_PENALTY = 0.25
MAX_CONTEXT_CREDIT = 262144
CONTEXT_CREDIT_UNIT = 65536

_POSITIVE_PIXEL_STATUSES = frozenset({"verified", "pixel-agent-viable"})
_NEGATIVE_EVIDENCE_STATUSES = frozenset({
    "not-agent-viable",
    "unsupported-until-revalidated",
    "blocked",
})
_CPU_BACKENDS = frozenset({"", "cpu", "none", "unknown"})


def normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def normalize_backend(value: Any) -> str:
    """Backend key; ``none``/``unknown``/empty (Windows no-GPU) mean ``cpu``."""
    key = normalize_key(value)
    return "cpu" if key in _CPU_BACKENDS else key


def normalize_host_arch(value: Any) -> str:
    key = normalize_key(value or "unknown")
    if key in {"aarch64", "arm64"}:
        return "arm64"
    if key in {"x86-64", "x86_64", "amd64", "x64"}:
        return "amd64"
    return key or "unknown"


def list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def value_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return normalize_key(value) not in {"", "0", "false", "off", "no"}


def memory_class(backend: Any, memory_type: Any, vram_mb: Any) -> str:
    """``unified`` (Apple, APUs), ``cpu`` (no GPU) or ``discrete``."""
    backend_key = normalize_backend(backend)
    if backend_key == "apple" or normalize_key(memory_type) == "unified":
        return "unified"
    try:
        vram = float(vram_mb or 0)
    except (TypeError, ValueError):
        vram = 0.0
    if backend_key == "cpu" or vram <= 0:
        return "cpu"
    return "discrete"


def usable_memory_gb(backend: Any, memory_type: Any, vram_mb: Any, ram_gb: Any) -> tuple[float, str]:
    """Memory budget for the model, in GiB, and a label for reasons."""
    mclass = memory_class(backend, memory_type, vram_mb)
    ram = float(ram_gb or 0)
    if mclass == "unified":
        # Unified-memory machines share RAM with the OS, Docker services, and
        # KV cache. Use only a bounded share for the model pick so 32GB-class
        # Macs/APUs are not handed a model that technically fits but thrashes.
        return max(ram * 0.55, 2.0), "unified system memory"
    if mclass == "cpu":
        return min(max(ram * 0.35, 3.0), 8.0), "system RAM"
    return float(vram_mb) / 1024.0, "GPU VRAM"


def hardware_matching_profiles(model: dict[str, Any], backend: Any, memory_type: Any,
                               vram_mb: Any, host_arch: Any,
                               ram_gb: Any = None) -> list[dict[str, Any]]:
    """Return profiles anchored to this hardware before system-RAM filtering.

    Once a catalog model has a profile for this exact backend/architecture/
    memory envelope, that profile is its safety contract. If the system-RAM
    requirement is not met, callers must not silently score the same model as
    though the hardware-specific profile did not exist.
    """
    backend_key = normalize_backend(backend)
    memory_key = normalize_key(memory_type)
    arch_key = normalize_host_arch(host_arch)
    try:
        vram_gb = float(vram_mb or 0) / 1024.0
    except (TypeError, ValueError):
        vram_gb = 0.0
    matches: list[dict[str, Any]] = []
    for profile in model.get("runtime_profiles", []) or []:
        if not isinstance(profile, dict):
            continue
        if normalize_key(profile.get("backend")) and normalize_backend(profile.get("backend")) != backend_key:
            continue
        allowed_arches = {normalize_host_arch(item) for item in list_value(profile.get("host_arch"))}
        if allowed_arches and arch_key not in allowed_arches:
            continue
        required_memory_type = normalize_key(profile.get("memory_type"))
        if required_memory_type and required_memory_type != memory_key:
            continue
        try:
            # A RAM ceiling scopes the profile to a class of machines; it is
            # not an unmet prerequisite on machines above that class.
            if ram_gb is not None and profile.get("system_ram_max_gb") is not None and float(ram_gb) > float(profile["system_ram_max_gb"]):
                continue
            if profile.get("vram_min_gb") is not None and vram_gb < float(profile["vram_min_gb"]):
                continue
            if profile.get("vram_max_gb") is not None and vram_gb > float(profile["vram_max_gb"]):
                continue
        except (TypeError, ValueError):
            continue
        matches.append(profile)
    return matches


def matching_runtime_profile(model: dict[str, Any], backend: Any, memory_type: Any,
                             vram_mb: Any, ram_gb: Any, host_arch: Any) -> dict[str, Any] | None:
    for profile in hardware_matching_profiles(model, backend, memory_type, vram_mb, host_arch, ram_gb):
        try:
            if profile.get("system_ram_min_gb") is not None and float(ram_gb or 0) < float(profile["system_ram_min_gb"]):
                continue
            if profile.get("system_ram_max_gb") is not None and float(ram_gb or 0) > float(profile["system_ram_max_gb"]):
                continue
        except (TypeError, ValueError):
            continue
        return profile
    return None


def family_allowed(model: dict[str, Any], profile: str) -> bool:
    family = normalize_key(model.get("family"))
    if profile == "gemma4":
        # Keep the tiny Qwen bootstrap fallback available for the minimum
        # tier, but otherwise honor the Gemma profile choice.
        return family == "gemma4" or model.get("id") == "qwen3.5-2b-q4"
    return family != "gemma4"


def install_recommendation_allowed(model: dict[str, Any]) -> bool:
    return bool(model.get("gguf_url")) and value_enabled(model.get("install_recommendation", True))


def _status(model: dict[str, Any], flat_key: str, app_key: str) -> str:
    if flat_key in model:
        return normalize_key(model.get(flat_key))
    compatibility = model.get("app_compatibility")
    entry = compatibility.get(app_key) if isinstance(compatibility, dict) else None
    return normalize_key(entry.get("status")) if isinstance(entry, dict) else ""


def pixel_agent_status(model: dict[str, Any]) -> str:
    return _status(model, "pixel_agent_status", "pixel_agent")


def agent_viability_status(model: dict[str, Any]) -> str:
    return _status(model, "agent_viability_status", "agent_viability")


def pixel_agent_ready(model: dict[str, Any]) -> bool:
    """Require an explicit real-Pixel capability verdict for the Pixel route."""
    return pixel_agent_status(model) in _POSITIVE_PIXEL_STATUSES


def evidence_adjustment(model: dict[str, Any]) -> int:
    pixel = pixel_agent_status(model)
    agent = agent_viability_status(model)
    adjustment = EVIDENCE_WEIGHT if pixel in _POSITIVE_PIXEL_STATUSES else 0
    if pixel in _NEGATIVE_EVIDENCE_STATUSES or agent in _NEGATIVE_EVIDENCE_STATUSES:
        adjustment -= EVIDENCE_WEIGHT
    return adjustment


def _selection(model: dict[str, Any]) -> dict[str, Any]:
    value = model.get("selection")
    return value if isinstance(value, dict) else {}


def selection_priority(model: dict[str, Any], mclass: str) -> int:
    """Curated priority for this memory class; 0 means never auto-selected.

    Entries without a ``selection`` block (imports, synthetic catalogs) share
    :data:`DEFAULT_SELECTION_PRIORITY`, below every curated default.
    """
    selection = _selection(model)
    if not selection:
        return DEFAULT_SELECTION_PRIORITY
    try:
        return int(selection.get(mclass, DEFAULT_SELECTION_PRIORITY))
    except (TypeError, ValueError):
        return DEFAULT_SELECTION_PRIORITY


def minimum_capacity_gib(model: dict[str, Any], mclass: str) -> float:
    floors = _selection(model).get("min_capacity_gib")
    if not isinstance(floors, dict):
        return 0.0
    try:
        return float(floors.get(mclass) or 0)
    except (TypeError, ValueError):
        return 0.0


def size_within_ceiling(model: dict[str, Any], max_size_mb: float) -> bool:
    """True if ``model`` respects an optional tier size ceiling.

    ``max_size_mb`` <= 0 means no ceiling. A small tolerance absorbs rounding
    differences between the tier map's LLM_MODEL_SIZE_MB and the catalog's
    size_mb for the same model.
    """
    if not max_size_mb or max_size_mb <= 0:
        return True
    size_mb = float(model.get("size_mb") or 0)
    return size_mb <= max_size_mb + max(max_size_mb * 0.02, 64.0)


def weights_gib(model: dict[str, Any]) -> float:
    try:
        size_bytes = float(model.get("size_bytes") or 0)
    except (TypeError, ValueError):
        size_bytes = 0.0
    if size_bytes > 0:
        return size_bytes / 1024.0 ** 3
    try:
        return float(model.get("size_mb") or 0) / 1024.0
    except (TypeError, ValueError):
        return 0.0


class Candidate:
    """One catalog model planned for one hardware envelope.

    A plain class (not a dataclass) so the module also loads through
    importlib without a sys.modules entry, like model_memory.
    """

    __slots__ = (
        "model", "runtime_profile", "context_length", "required_gb", "estimate",
        "architecture_estimate", "authored_estimate", "meets_min_context",
        "memory_class", "capacity_gb", "fit_margin_gb", "priority", "evidence",
    )

    def __init__(self, *, model: dict[str, Any], runtime_profile: dict[str, Any] | None,
                 context_length: int, required_gb: float, estimate: MemoryEstimate,
                 architecture_estimate: bool, authored_estimate: bool,
                 meets_min_context: bool, memory_class: str, capacity_gb: float,
                 fit_margin_gb: float, priority: int, evidence: int) -> None:
        self.model = model
        self.runtime_profile = runtime_profile
        self.context_length = context_length
        self.required_gb = required_gb
        self.estimate = estimate
        self.architecture_estimate = architecture_estimate
        self.authored_estimate = authored_estimate
        self.meets_min_context = meets_min_context
        self.memory_class = memory_class
        self.capacity_gb = capacity_gb
        self.fit_margin_gb = fit_margin_gb
        self.priority = priority
        self.evidence = evidence

    @property
    def id(self) -> str:
        return str(self.model.get("id") or "")

    def as_model(self) -> dict[str, Any]:
        """The legacy ranker shape: the model at its chosen context."""
        planned = {**self.model, "context_length": self.context_length}
        if self.context_length != int(self.model.get("context_length") or 0):
            planned["max_context_length"] = (
                self.model.get("max_context_length") or self.model.get("context_length")
            )
        if self.runtime_profile is not None:
            planned["_runtime_profile"] = self.runtime_profile
        else:
            planned.pop("_runtime_profile", None)
        planned["_selection"] = self.summary()
        return planned

    def summary(self) -> dict[str, Any]:
        return {
            "required_gb": self.required_gb,
            "context_length": self.context_length,
            "memory_class": self.memory_class,
            "capacity_gb": round(self.capacity_gb, 2),
            "fit_margin_gb": self.fit_margin_gb,
            "meets_min_context": self.meets_min_context,
            "priority": self.priority,
            "evidence": self.evidence,
            "estimate_source": (
                "runtime-profile" if self.authored_estimate
                else self.estimate.method
            ),
            "estimate": self.estimate.as_dict(),
        }


def candidate_fits(candidate: Candidate) -> bool:
    """The one fit gate for a planned candidate (see the module docstring).

    Architecture estimates must leave ``fit_margin_gib`` free (discrete GPUs:
    max(0.25 GiB, 3%); unified and CPU capacities are already bounded shares
    of RAM). Legacy estimates and hand-measured runtime-profile budgets keep
    the historical +0.25 GiB tolerance.
    """
    return memory_fits(
        candidate.required_gb, candidate.capacity_gb, candidate.memory_class,
        architecture_estimate=candidate.architecture_estimate,
    )


def plan_candidate(model: dict[str, Any], *, capacity_gb: float, mclass: str,
                   backend: Any, memory_type: Any, vram_mb: Any, ram_gb: Any,
                   host_arch: Any, min_context: int = 0,
                   priority: int | None = None) -> Candidate | None:
    """Fit ``model`` to this hardware, or return None when it cannot run here.

    A hardware-matching runtime profile is the model's safety contract here:
    its context is fixed and, if it fails its RAM gate, the model is dropped.
    Without one, contexts are tried from the catalog default down (see
    :func:`model_memory.context_candidates`), preferring those that meet
    ``min_context``.
    """
    hardware_profiles = hardware_matching_profiles(
        model, backend, memory_type, vram_mb, host_arch, ram_gb
    )
    runtime_profile = matching_runtime_profile(
        model, backend, memory_type, vram_mb, ram_gb, host_arch
    )
    if runtime_profile is None and hardware_profiles:
        return None
    include_host = mclass == "cpu"
    margin = fit_margin_gib(capacity_gb, mclass)
    base_priority = selection_priority(model, mclass) if priority is None else priority
    evidence = evidence_adjustment(model)

    def _candidate(context: int, estimate: MemoryEstimate, required: float,
                   authored: bool) -> Candidate:
        architecture = (not authored) and estimate.method == "architecture"
        return Candidate(
            model=model,
            runtime_profile=runtime_profile,
            context_length=int(context),
            required_gb=round(required, 2),
            estimate=estimate,
            architecture_estimate=architecture,
            authored_estimate=authored,
            meets_min_context=(not min_context) or int(context) >= int(min_context),
            memory_class=mclass,
            capacity_gb=float(capacity_gb),
            fit_margin_gb=margin if architecture else -LEGACY_FIT_TOLERANCE_GIB,
            priority=base_priority,
            evidence=evidence,
        )

    if runtime_profile is not None:
        try:
            context = int(runtime_profile.get("context_length") or model.get("context_length") or 0)
        except (TypeError, ValueError):
            context = int(model.get("context_length") or 0)
        estimate = estimate_for_runtime(model, context_length=context, runtime_profile=runtime_profile)
        authored = authored_profile_estimate(runtime_profile)
        required = authored or (estimate.total_gib if include_host else estimate.device_gib)
        candidate = _candidate(context, estimate, required, bool(authored))
        return candidate if candidate_fits(candidate) else None

    contexts = context_candidates(model, min_context=min_context)
    ordered = (
        [context for context in contexts if context >= min_context]
        + [context for context in contexts if context < min_context]
    )
    for context in ordered:
        estimate = estimate_model_memory(model, context_length=context)
        required = estimate.total_gib if include_host else estimate.device_gib
        candidate = _candidate(context, estimate, required, False)
        if candidate_fits(candidate):
            return candidate
    return None


def plan_model_context(model: dict[str, Any], *, capacity_gb: float, backend: Any,
                       memory_type: Any, vram_mb: Any, ram_gb: Any, host_arch: Any,
                       min_context: int = 0,
                       preferred_context: int | None = None) -> dict[str, Any]:
    """The context to serve ``model`` at on this hardware.

    This is the install policy (:func:`plan_candidate`, the same code the
    ranker uses) applied to one model, so a dashboard switch, a restore of
    the installer's pick and the installer itself serve the same context:
    start at ``preferred_context`` (the context already chosen for this
    model, e.g. the installer's recommendation) or the catalog default; raise
    it to ``min_context`` (the Hermes floor) when the model's native maximum
    allows and it fits; step down only when it does not fit. A matching
    runtime profile fixes the context.

    Returns ``fits: False`` with the unchanged context when no context fits
    (the caller keeps today's behavior; the model may run partly offloaded).
    """
    mclass = memory_class(backend, memory_type, vram_mb)
    default = _int_or_zero(model.get("context_length"))
    declared_max = declared_max_context(model)
    native = declared_max or default
    # A context already chosen for this model (by the installer or the
    # owner) is honored as the starting point, as activation always did; the
    # floor can raise it only as far as the catalog's native maximum. A
    # preferred context above a declared native maximum (a stale .env CTX_SIZE
    # or installer record) is clamped to it: llama.cpp caps the slot at the
    # model's training context, so a larger request can never be served.
    preferred = _int_or_zero(preferred_context)
    if declared_max and preferred > declared_max:
        preferred = declared_max
    planned = model
    if preferred and preferred != default:
        planned = {**model, "context_length": preferred, "max_context_length": max(native, preferred)}
    floor = max(_int_or_zero(min_context), 0)
    candidate = plan_candidate(
        planned, capacity_gb=capacity_gb, mclass=mclass, backend=backend,
        memory_type=memory_type, vram_mb=vram_mb, ram_gb=ram_gb,
        host_arch=host_arch, min_context=floor,
    )
    if candidate is None:
        context = preferred or default
        return {
            "context_length": context,
            "fits": False,
            "meets_min_context": (not floor) or context >= floor,
            "min_context": floor,
            "max_context_length": native,
            "memory_class": mclass,
            "capacity_gb": round(float(capacity_gb or 0), 2),
            "required_gb": None,
            "runtime_profile": None,
            "estimate_source": None,
        }
    summary = candidate.summary()
    return {
        "context_length": candidate.context_length,
        "fits": True,
        "meets_min_context": candidate.meets_min_context,
        "min_context": floor,
        "max_context_length": max(native, candidate.context_length),
        "memory_class": mclass,
        "capacity_gb": summary["capacity_gb"],
        "required_gb": candidate.required_gb,
        "runtime_profile": (candidate.runtime_profile or {}).get("id"),
        "estimate_source": summary["estimate_source"],
    }


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def declared_max_context(model: dict[str, Any]) -> int:
    """The catalog's declared native maximum context, or 0 when undeclared.

    ``max_context_length`` is the model's native (config.json / GGUF
    training) context; tests/test_model_library_native_context.py keeps it
    at or below the GGUF header value. The dashboard's normalized entries
    (performance_oracle.normalize_catalog_entry) fill ``max_context_length``
    from ``context_length`` when the catalog declares none and mark that with
    ``native_context_declared: False``; such an entry has no known ceiling
    here, so an owner's larger context is not clamped to the catalog default.
    """
    if model.get("native_context_declared") is False:
        return 0
    return _int_or_zero(model.get("max_context_length"))


def rank_key(candidate: Candidate, profile: str, *,
             include_size_tiebreak: bool = True) -> tuple:
    family_match = 1 if profile == "gemma4" and normalize_key(candidate.model.get("family")) == "gemma4" else 0
    context_credit = min(candidate.context_length, MAX_CONTEXT_CREDIT) / float(CONTEXT_CREDIT_UNIT)
    headroom = candidate.required_gb / max(candidate.capacity_gb, 1.0) > HEADROOM_RATIO
    return (
        family_match,
        1 if candidate.meets_min_context else 0,
        candidate.priority + candidate.evidence,
        round(context_credit - (HEADROOM_PENALTY if headroom else 0.0), 6),
        round(weights_gib(candidate.model), 6) if include_size_tiebreak else 0.0,
    )


def rank_catalog_models(
    catalog: Iterable[dict[str, Any]],
    *,
    capacity_gb: float,
    profile: str,
    installable_only: bool,
    backend: Any,
    memory_type: Any,
    vram_mb: Any,
    ram_gb: Any,
    host_arch: Any,
    max_size_mb: float = 0,
    agent_ready_only: bool = False,
    min_context: int = 0,
    require_min_context: bool = False,
    include_size_tiebreak: bool = True,
    installable: Callable[[dict[str, Any]], bool] | None = None,
) -> list[Candidate]:
    """Rank every eligible catalog model that fits this hardware.

    Eligibility: installable (when asked), family allowed, curated priority
    above 0 for this memory class, capacity at least the entry's
    ``min_capacity_gib``, tier size ceiling, runtime-profile RAM gates, and a
    context that fits. A size ceiling that excludes every fitting model is a
    preference, not a hard limit: the ranking is retried without it.
    """
    models = list(catalog)
    mclass = memory_class(backend, memory_type, vram_mb)
    allowed = installable or install_recommendation_allowed

    def _pool(enforce_ceiling: bool) -> list[Candidate]:
        pool: list[Candidate] = []
        for model in models:
            if installable_only and not allowed(model):
                continue
            if agent_ready_only and not pixel_agent_ready(model):
                continue
            if not family_allowed(model, profile):
                continue
            if enforce_ceiling and not size_within_ceiling(model, max_size_mb):
                continue
            priority = selection_priority(model, mclass)
            if priority <= 0:
                continue
            if capacity_gb < minimum_capacity_gib(model, mclass):
                continue
            candidate = plan_candidate(
                model, capacity_gb=capacity_gb, mclass=mclass, backend=backend,
                memory_type=memory_type, vram_mb=vram_mb, ram_gb=ram_gb,
                host_arch=host_arch, min_context=min_context, priority=priority,
            )
            if candidate is None:
                continue
            if require_min_context and not candidate.meets_min_context:
                continue
            pool.append(candidate)
        pool.sort(
            key=lambda item: rank_key(item, profile, include_size_tiebreak=include_size_tiebreak),
            reverse=True,
        )
        return pool

    ranked = _pool(enforce_ceiling=True)
    if not ranked and max_size_mb and max_size_mb > 0:
        ranked = _pool(enforce_ceiling=False)
    return ranked


def check_fit(model: dict[str, Any], *, context_length: int, capacity_gb: float,
              mclass: str, runtime_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Would ``model`` fit at ``context_length``? Used by the Hermes re-check.

    A runtime profile's authored estimate applies only at the profile's own
    context; at any other context its cache settings feed the estimator.
    A context above the declared native maximum never fits (llama.cpp caps
    the slot there, so the raise could not be served), whatever the memory;
    ``above_native_max`` says so.
    """
    native_max = declared_max_context(model)
    above_native_max = bool(native_max) and int(context_length) > native_max
    estimate = estimate_for_runtime(model, context_length=context_length, runtime_profile=runtime_profile)
    authored = 0.0
    if runtime_profile is not None:
        try:
            profile_context = int(runtime_profile.get("context_length") or 0)
        except (TypeError, ValueError):
            profile_context = 0
        if profile_context == int(context_length):
            authored = authored_profile_estimate(runtime_profile)
    required = authored or (estimate.total_gib if mclass == "cpu" else estimate.device_gib)
    architecture = (not authored) and estimate.method == "architecture"
    margin = fit_margin_gib(capacity_gb, mclass) if architecture else -LEGACY_FIT_TOLERANCE_GIB
    fits = candidate_fits(Candidate(
        model=model, runtime_profile=runtime_profile, context_length=int(context_length),
        required_gb=round(required, 2), estimate=estimate,
        architecture_estimate=architecture, authored_estimate=bool(authored),
        meets_min_context=True, memory_class=mclass, capacity_gb=float(capacity_gb),
        fit_margin_gb=margin, priority=0, evidence=0,
    ))
    return {
        "fits": bool(fits) and not above_native_max,
        "model_id": model.get("id"),
        "context_length": int(context_length),
        "max_context_length": native_max or None,
        "above_native_max": above_native_max,
        "runtime_profile": (runtime_profile or {}).get("id"),
        "required_gb": round(required, 2),
        "capacity_gb": round(capacity_gb, 2),
        "memory_class": mclass,
        "margin_gb": margin,
        "estimate_source": "runtime-profile" if authored else estimate.method,
        "estimate": estimate.as_dict(),
    }
