"""Service template endpoints."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from config import EXTENSION_CATALOG, GPU_BACKEND, SERVICES, TEMPLATES, USER_EXTENSIONS_DIR
from security import verify_api_key

logger = logging.getLogger(__name__)

# Services defined in docker-compose.base.yml — always running, no compose toggle
_BASE_COMPOSE_SERVICES = frozenset({"llama-server", "open-webui", "dashboard", "dashboard-api"})

router = APIRouter(tags=["templates"])


@router.get("/api/templates")
async def list_templates(api_key: str = Depends(verify_api_key)):
    """List all available service templates."""
    return {"templates": TEMPLATES}


@router.post("/api/templates/{template_id}/preview")
async def preview_template(template_id: str, api_key: str = Depends(verify_api_key)):
    """Preview what applying a template would change."""
    template = next((t for t in TEMPLATES if t["id"] == template_id), None)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")

    from helpers import get_cached_services, get_all_services
    from routers.extensions import _compute_extension_status

    service_list = get_cached_services()
    if service_list is None:
        service_list = await get_all_services()
    services_by_id = {s.id: s for s in service_list}
    catalog_by_id = {e["id"]: e for e in EXTENSION_CATALOG}

    to_enable = []
    already_enabled = []
    incompatible = []
    in_progress = []
    has_errors = []
    warnings = []

    for svc_id in template.get("services", []):
        svc_config = SERVICES.get(svc_id)
        svc_status = services_by_id.get(svc_id)

        # Core services are always running — treat as already enabled
        if svc_id in _BASE_COMPOSE_SERVICES:
            already_enabled.append(svc_id)
            continue

        # Compute rich extension status (installing / setting_up / error / enabled / …)
        # by reusing the same logic the catalog endpoint uses, so template state
        # stays consistent with what the UI shows on individual extension cards.
        ext = catalog_by_id.get(svc_id)
        ext_status = (
            _compute_extension_status(ext, services_by_id) if ext else None
        )

        if ext_status == "error":
            has_errors.append(svc_id)
            continue
        if ext_status in ("installing", "setting_up"):
            in_progress.append(svc_id)
            continue
        if ext_status == "enabled" or (svc_status and svc_status.status == "healthy"):
            already_enabled.append(svc_id)
            continue

        # Check GPU compatibility (from manifest data in SERVICES)
        if svc_config:
            gpu_backends = svc_config.get("gpu_backends", ["amd", "nvidia", "apple"])
            if GPU_BACKEND != "apple" and GPU_BACKEND not in gpu_backends and "all" not in gpu_backends:
                incompatible.append(svc_id)
                warnings.append(f"{svc_id} gpu_backends {gpu_backends} - your system: {GPU_BACKEND}")
                continue

        to_enable.append(svc_id)

    return {
        "template": {"id": template["id"], "name": template["name"]},
        "changes": {
            "to_enable": to_enable,
            "already_enabled": already_enabled,
            "incompatible": incompatible,
            "in_progress": in_progress,
            "has_errors": has_errors,
        },
        "warnings": warnings,
    }


@router.post("/api/templates/{template_id}/apply")
async def apply_template(template_id: str, api_key: str = Depends(verify_api_key)):
    """Apply a template by enabling its listed services (additive only).

    Uses the same dep-aware enable flow as enable_extension with
    auto_enable_deps=True — transitive deps are resolved and activated
    before each service.
    """
    template = next((t for t in TEMPLATES if t["id"] == template_id), None)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")

    from helpers import get_cached_services, get_all_services
    from routers.extensions import (
        _activate_service, _extensions_lock, _call_agent, _call_agent_hook,
        _get_missing_deps_transitive, _read_direct_deps, _validate_service_id,
        _install_from_library, _is_installable,
        _call_agent_invalidate_compose_cache,
    )

    # Blocking sections run in the thread pool so the event loop stays
    # responsive: urllib install fetches use 300s timeouts, and host-agent
    # calls block on the network. _extensions_lock cannot cross thread
    # boundaries, so each lock acquisition runs inside a single off-loop call.
    def _install_with_lock(sid: str) -> None:
        with _extensions_lock():
            _install_from_library(sid)
            _call_agent_invalidate_compose_cache()

    def _activate_with_lock(sid: str, missing_deps, prior_results):
        deps_enabled: list[str] = []
        with _extensions_lock():
            for dep in missing_deps:
                if dep in prior_results:
                    prior_outcome = prior_results[dep]
                    prior_succeeded = prior_outcome in {
                        "already_enabled", "core_service", "enabled",
                        "enabled_as_dependency", "library_installed",
                    }
                    if prior_succeeded:
                        continue
                    raise HTTPException(
                        status_code=424,
                        detail=f"Dependency {dep} was not enabled: {prior_outcome}",
                    )
                dep_result = _activate_service(dep)
                if dep_result.get("action") == "enabled":
                    deps_enabled.append(dep)
            main_result = _activate_service(sid)
            if deps_enabled or main_result.get("action") == "enabled":
                _call_agent_invalidate_compose_cache()
        return deps_enabled, main_result

    service_list = get_cached_services()
    if service_list is None:
        service_list = await get_all_services()
    services_by_id = {s.id: s for s in service_list}

    results = {}
    enabled_services = []
    library_installed: list[str] = []
    warnings: list[str] = []

    for svc_id in template.get("services", []):
        # Skip services already healthy
        svc_status = services_by_id.get(svc_id)
        if svc_status and svc_status.status == "healthy":
            results[svc_id] = "already_enabled"
            continue

        # Skip core services (defined in docker-compose.base.yml, always running)
        # These have no individual compose.yaml to toggle — they're always on.
        if svc_id in _BASE_COMPOSE_SERVICES:
            results[svc_id] = "core_service"
            continue

        svc_config = SERVICES.get(svc_id)
        if svc_config:
            gpu_backends = svc_config.get("gpu_backends", ["amd", "nvidia", "apple"])
            if GPU_BACKEND != "apple" and GPU_BACKEND not in gpu_backends and "all" not in gpu_backends:
                detail = f"requires one of {gpu_backends}; current backend is {GPU_BACKEND}"
                results[svc_id] = f"skipped: incompatible GPU backend: {detail}"
                warnings.append(f"{svc_id}: {detail}")
                continue

        try:
            _validate_service_id(svc_id)

            # Library extension not yet installed → copy from library first.
            # _install_from_library produces a directory with compose.yaml
            # already in place (not compose.yaml.disabled), so _activate_service
            # will report "already_enabled" afterwards — we still want to start it.
            if _is_installable(svc_id) and not (USER_EXTENSIONS_DIR / svc_id).is_dir():
                try:
                    await asyncio.to_thread(_install_with_lock, svc_id)
                    await asyncio.to_thread(_call_agent_hook, svc_id, "post_install")
                    library_installed.append(svc_id)
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                    logger.warning(
                        "Template apply failed to install library extension %s: %s",
                        svc_id, detail,
                    )
                    results[svc_id] = f"skipped: install failed: {detail}"
                    continue

            # Dep-aware enable: resolve transitive deps, activate leaves first.
            # _activate_service checks both user-installed and built-in extension dirs.
            missing_deps = await asyncio.to_thread(_get_missing_deps_transitive, svc_id)

            deps_enabled, result = await asyncio.to_thread(
                _activate_with_lock, svc_id, missing_deps, results,
            )
            for dep in deps_enabled:
                enabled_services.append(dep)
                results[dep] = "enabled_as_dependency"

            action = result.get("action", "skipped")
            if svc_id in library_installed:
                results[svc_id] = "library_installed"
            else:
                results[svc_id] = action
            # Always start via host agent unless already healthy
            # "already_enabled" means compose file exists but container may not be running
            if action in ("enabled", "already_enabled"):
                enabled_services.append(svc_id)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            logger.warning("Template apply skipped %s: %s", svc_id, detail)
            results[svc_id] = f"skipped: {detail}"

    # A dependency may also appear later as a top-level template service.
    # Start each service once while preserving dependency order.
    enabled_services = list(dict.fromkeys(enabled_services))

    # Start enabled services via the same host-agent lifecycle used by the
    # individual extension endpoint. Built-in extensions are valid host-agent
    # targets too; the old user-extension-only gate left them enabled on disk
    # but stopped until a manual full-stack restart.
    failed_services: list[str] = []
    for svc_id in enabled_services:
        direct_deps = await asyncio.to_thread(_read_direct_deps, svc_id)
        blocked_deps = [dep for dep in direct_deps if dep in failed_services]
        if blocked_deps:
            results[svc_id] = "enabled_but_dependency_failed"
            failed_services.append(svc_id)
            warnings.append(
                f"{svc_id}: not started because dependencies failed: {', '.join(blocked_deps)}",
            )
            continue

        pre_start_ok = await asyncio.to_thread(_call_agent_hook, svc_id, "pre_start")
        if not pre_start_ok:
            results[svc_id] = "enabled_but_pre_start_failed"
            failed_services.append(svc_id)
            warnings.append(f"{svc_id}: pre_start hook failed; service was not started")
            continue

        start_ok = await asyncio.to_thread(_call_agent, "start", svc_id)
        if not start_ok:
            if svc_id in library_installed:
                results[svc_id] = "library_installed_but_start_failed"
            else:
                results[svc_id] = "enabled_but_start_failed"
            failed_services.append(svc_id)

        post_start_ok = await asyncio.to_thread(_call_agent_hook, svc_id, "post_start")
        if not post_start_ok:
            warnings.append(f"{svc_id}: post_start hook failed; manual configuration may be needed")

    skipped_services = [
        service_id
        for service_id, outcome in results.items()
        if isinstance(outcome, str) and outcome.startswith("skipped:")
    ]
    restart_required = bool(failed_services)

    return {
        "template_id": template_id,
        "results": results,
        "enabled_count": len(enabled_services),
        "started_count": len(enabled_services) - len(failed_services),
        "library_installed": library_installed,
        "failed_services": failed_services,
        "skipped_services": skipped_services,
        "warnings": warnings,
        "restart_required": restart_required,
    }
