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


def _runtime_dependency_order(
    service_id: str,
    read_direct_deps,
    *,
    _visiting: set[str] | None = None,
    _visited: set[str] | None = None,
    _order: list[str] | None = None,
) -> list[str]:
    """Return every dependency in leaves-first runtime start order."""
    if _visiting is None:
        _visiting = set()
    if _visited is None:
        _visited = set()
    if _order is None:
        _order = []

    if service_id in _visiting:
        raise HTTPException(
            status_code=400,
            detail=f"Circular dependency detected involving: {service_id}",
        )
    if service_id in _visited:
        return _order

    _visiting.add(service_id)
    for dep in read_direct_deps(service_id):
        _runtime_dependency_order(
            dep,
            read_direct_deps,
            _visiting=_visiting,
            _visited=_visited,
            _order=_order,
        )
        if dep not in _order:
            _order.append(dep)
    _visiting.remove(service_id)
    _visited.add(service_id)
    return _order


def _gpu_backend_error(service_id: str) -> str | None:
    """Return a compatibility error for a service on the current GPU backend."""
    service_config = SERVICES.get(service_id)
    if service_config is None:
        service_config = next(
            (entry for entry in EXTENSION_CATALOG if entry.get("id") == service_id),
            None,
        )
    if not service_config or GPU_BACKEND == "apple":
        return None

    gpu_backends = service_config.get("gpu_backends", ["amd", "nvidia", "apple"])
    if "all" in gpu_backends or GPU_BACKEND in gpu_backends:
        return None
    return f"requires one of {gpu_backends}; current backend is {GPU_BACKEND}"


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

        compatibility_error = _gpu_backend_error(svc_id)
        if compatibility_error:
            incompatible.append(svc_id)
            warnings.append(f"{svc_id}: {compatibility_error}")
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
        _has_error_progress, _sync_extension_config, _write_error_progress,
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
            # Validate the whole dependency plan before the first compose
            # mutation. A later incompatibility must not leave earlier deps
            # silently enabled.
            for dep in missing_deps:
                compatibility_error = _gpu_backend_error(dep)
                if compatibility_error:
                    raise HTTPException(
                        status_code=424,
                        detail=f"Dependency {dep} is incompatible: {compatibility_error}",
                    )
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

            try:
                for dep in missing_deps:
                    if dep in prior_results:
                        continue
                    dep_result = _activate_service(dep)
                    if dep_result.get("action") == "enabled":
                        deps_enabled.append(dep)
                main_result = _activate_service(sid)
            except HTTPException as exc:
                # Compose activation is additive. Surface any dependency that
                # was already enabled before a later activation failed so the
                # caller can start and report it instead of losing that state.
                if deps_enabled:
                    _call_agent_invalidate_compose_cache()
                return deps_enabled, None, exc
            if deps_enabled or main_result.get("action") == "enabled":
                _call_agent_invalidate_compose_cache()
        return deps_enabled, main_result, None

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

        compatibility_error = _gpu_backend_error(svc_id)
        if compatibility_error:
            results[svc_id] = f"skipped: incompatible GPU backend: {compatibility_error}"
            warnings.append(f"{svc_id}: {compatibility_error}")
            continue

        try:
            _validate_service_id(svc_id)

            # Library extension not yet installed → copy from library first.
            # _install_from_library produces a directory with compose.yaml
            # already in place (not compose.yaml.disabled), so _activate_service
            # will report "already_enabled" afterwards — we still want to start it.
            installable = _is_installable(svc_id)
            installed_dir_exists = (USER_EXTENSIONS_DIR / svc_id).is_dir()
            has_install_error = (
                await asyncio.to_thread(_has_error_progress, svc_id)
                if installable and installed_dir_exists
                else False
            )
            needs_library_install = installable and (
                not installed_dir_exists or has_install_error
            )
            if needs_library_install:
                try:
                    await asyncio.to_thread(_install_with_lock, svc_id)
                    library_installed.append(svc_id)
                    config_synced = await asyncio.to_thread(_sync_extension_config, svc_id)
                    if not config_synced:
                        message = "extension config sync failed; retry template apply after restoring the host agent"
                        await asyncio.to_thread(_write_error_progress, svc_id, message)
                        results[svc_id] = f"skipped: {message}"
                        warnings.append(f"{svc_id}: {message}")
                        continue
                    post_install_ok = await asyncio.to_thread(
                        _call_agent_hook, svc_id, "post_install",
                    )
                    if not post_install_ok:
                        message = "post_install hook failed; retry template apply after fixing the hook"
                        await asyncio.to_thread(_write_error_progress, svc_id, message)
                        results[svc_id] = f"skipped: {message}"
                        warnings.append(f"{svc_id}: {message}")
                        continue
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                    logger.warning(
                        "Template apply failed to install library extension %s: %s",
                        svc_id, detail,
                    )
                    results[svc_id] = f"skipped: install failed: {detail}"
                    continue

            # Resolve the complete runtime dependency tree separately from the
            # activation plan. An existing compose.yaml means enabled on disk,
            # but does not prove that the dependency container is running.
            runtime_deps = await asyncio.to_thread(
                _runtime_dependency_order, svc_id, _read_direct_deps,
            )

            # Dep-aware enable: resolve missing deps, activate leaves first.
            # _activate_service checks both user-installed and built-in extension dirs.
            missing_deps = await asyncio.to_thread(_get_missing_deps_transitive, svc_id)

            deps_enabled, result, activation_error = await asyncio.to_thread(
                _activate_with_lock, svc_id, missing_deps, results,
            )
            for dep in deps_enabled:
                enabled_services.append(dep)
                results[dep] = "enabled_as_dependency"

            if activation_error is not None:
                detail = (
                    activation_error.detail
                    if isinstance(activation_error.detail, str)
                    else str(activation_error.detail)
                )
                results[svc_id] = f"skipped: {detail}"
                continue

            dependency_failed = False
            successful_outcomes = {
                "already_enabled", "core_service", "enabled",
                "enabled_as_dependency", "library_installed",
            }
            for dep in runtime_deps:
                dep_status = services_by_id.get(dep)
                if dep in _BASE_COMPOSE_SERVICES or (
                    dep_status and dep_status.status == "healthy"
                ):
                    continue
                prior_outcome = results.get(dep)
                if prior_outcome is not None and prior_outcome not in successful_outcomes:
                    results[svc_id] = f"skipped: dependency {dep} was not enabled: {prior_outcome}"
                    dependency_failed = True
                    break
                if prior_outcome is None:
                    results[dep] = "already_enabled"
                enabled_services.append(dep)
            if dependency_failed:
                continue

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

        if svc_id not in library_installed:
            # Built-ins declaring a setup/post_install hook (e.g. langfuse's
            # data-dir chown) have never run it when they were disabled at
            # install time; starting them without it reproduces the hook's
            # documented first-start failure. Hooks are no-ops when
            # undeclared and idempotent by lifecycle contract.
            post_install_ok = await asyncio.to_thread(
                _call_agent_hook, svc_id, "post_install",
            )
            if not post_install_ok:
                results[svc_id] = "enabled_but_post_install_failed"
                failed_services.append(svc_id)
                warnings.append(
                    f"{svc_id}: post_install hook failed; service was not started",
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
            continue

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
