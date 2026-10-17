"""Service template endpoints."""

import asyncio
import logging
from contextlib import ExitStack

import yaml
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
        if ext_status in ("enabled", "cli_installed") or (svc_status and svc_status.status == "healthy"):
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

    service_list = get_cached_services()
    if service_list is None:
        service_list = await get_all_services()
    # One worker owns the complete physical transaction. HTTP cancellation
    # cannot close its file locks while a host call is still running.
    return await asyncio.to_thread(_apply_template_guarded, template, service_list)


def _template_operation_ids(template, read_direct_deps, validate_service_id):
    """Collect reachable IDs without changing per-service cycle/error reporting."""
    visited = set()

    def visit(sid):
        if sid in visited:
            return
        try:
            validate_service_id(sid)
        except HTTPException:
            return
        visited.add(sid)
        try:
            dependencies = read_direct_deps(sid)
        except HTTPException:
            return  # The normal apply flow reports this service's invalid plan.
        for dep in dependencies:
            visit(dep)

    for sid in template.get("services", []):
        visit(sid)
    return sorted(visited)


def _apply_template_guarded(template, service_list):
    from routers.extensions import (
        _extension_operation_lock, _extensions_lock, _read_direct_deps,
        _validate_service_id,
    )

    def operation_ids():
        with _extensions_lock():
            return _template_operation_ids(template, _read_direct_deps, _validate_service_id)

    for _attempt in range(3):
        planned = operation_ids()
        with ExitStack() as locks:
            # A common order prevents opposite template orders from deadlocking.
            for sid in planned:
                locks.enter_context(_extension_operation_lock(sid))
            if operation_ids() != planned:
                continue  # Release all locks before acquiring a revised plan.
            return _apply_template_services(template, service_list)
    raise HTTPException(status_code=409, detail="Template dependencies changed during preparation; retry apply.")


def _healthy_template_definition(service_id):
    """A cached health result cannot bypass a disable completed before admission."""
    if service_id in _BASE_COMPOSE_SERVICES:
        return True
    from routers.extensions import _resolve_extension_dir, _installation_plan_service

    try:
        directory = _resolve_extension_dir(service_id)
    except HTTPException:
        return False
    # The selected user definition shadows bundled metadata, including type.
    if (directory / "compose.yaml").is_file():
        return True
    if (directory / "compose.yaml.disabled").exists():
        return False
    try:
        service = _installation_plan_service(service_id)
    except (ValueError, OSError, UnicodeError, yaml.YAMLError):
        return False  # Normal apply reports the missing or invalid definition.
    return isinstance(service, dict) and service.get("type", "docker") != "docker"


def _apply_template_services(template, service_list):
    from routers.extensions import (
        _activate_service, _extensions_lock, _call_agent_result, _call_agent_hook,
        _get_missing_deps_transitive, _read_direct_deps, _validate_service_id,
        _install_from_library, _is_installable,
        _call_agent_invalidate_compose_cache,
        _has_error_progress, _sync_extension_config, _write_error_progress,
        _compute_extension_status, _select_extensions_on_host,
    )

    # This entire lifecycle runs in the guarded worker. Keep global filesystem
    # sections short; per-service operation locks also cover hooks and starts.
    def _install_with_lock(sid: str) -> None:
        with _extensions_lock():
            _install_from_library(sid)
            _call_agent_invalidate_compose_cache()

    def _activate_with_lock(sid: str, missing_deps, prior_results):
        # _activate_service now validates bytes only. Publish the complete plan
        # through the authoritative host selector after releasing its shared
        # graph lock; the worker still owns every service-operation lock.
        plan = {}
        with _extensions_lock():
            for dep in missing_deps:
                compatibility_error = _gpu_backend_error(dep)
                if compatibility_error:
                    raise HTTPException(status_code=424,
                        detail=f"Dependency {dep} is incompatible: {compatibility_error}")
                if dep in prior_results:
                    if prior_results[dep] in {
                        "already_enabled", "core_service", "enabled",
                        "enabled_as_dependency", "library_installed",
                    }:
                        continue
                    raise HTTPException(status_code=424,
                        detail=f"Dependency {dep} was not enabled: {prior_results[dep]}")
                plan[dep] = _activate_service(dep)
            plan[sid] = _activate_service(sid)
        try:
            _select_extensions_on_host("enable", list(plan),
                expected_sha256={key: result["sha256"] for key, result in plan.items()})
        except HTTPException as exc:
            return [], None, exc
        deps_enabled = [dep for dep in missing_deps
                        if dep in plan and plan[dep].get("action") == "enabled"]
        main_result = plan[sid]
        if deps_enabled or main_result.get("action") == "enabled":
            _call_agent_invalidate_compose_cache()
        return deps_enabled, main_result, None

    # Validate cached health once, before activation can recreate an enabled
    # compose file. Both root and runtime-dependency shortcuts use this view.
    services_by_id = {
        service.id: service for service in service_list
        if service.status != "healthy" or _healthy_template_definition(service.id)
    }

    catalog_by_id = {entry["id"]: entry for entry in EXTENSION_CATALOG}
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

        # One-shot tools have no healthy daemon to observe. Reuse catalog
        # readiness so an already installed CLI is not invoked by reapplying.
        ext = catalog_by_id.get(svc_id)
        if ext and _compute_extension_status(ext, services_by_id) == "cli_installed":
            results[svc_id] = "already_enabled"
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
                _has_error_progress(svc_id)
                if installable and installed_dir_exists
                else False
            )
            needs_library_install = installable and (
                not installed_dir_exists or has_install_error
            )
            if needs_library_install:
                try:
                    _install_with_lock(svc_id)
                    library_installed.append(svc_id)
                    config_synced = _sync_extension_config(svc_id, preserve_existing=True)
                    if not config_synced:
                        message = "extension config sync failed; retry template apply after restoring the host agent"
                        _write_error_progress(svc_id, message)
                        results[svc_id] = f"skipped: {message}"
                        warnings.append(f"{svc_id}: {message}")
                        continue
                    post_install_ok = _call_agent_hook(svc_id, "post_install")
                    if not post_install_ok:
                        message = "post_install hook failed; retry template apply after fixing the hook"
                        _write_error_progress(svc_id, message)
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
            runtime_deps = _runtime_dependency_order(svc_id, _read_direct_deps)

            # Dep-aware enable: resolve missing deps, activate leaves first.
            # _activate_service checks both user-installed and built-in extension dirs.
            missing_deps = _get_missing_deps_transitive(svc_id)

            deps_enabled, result, activation_error = _activate_with_lock(svc_id, missing_deps, results)
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
        direct_deps = _read_direct_deps(svc_id)
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
            post_install_ok = _call_agent_hook(svc_id, "post_install")
            if not post_install_ok:
                results[svc_id] = "enabled_but_post_install_failed"
                failed_services.append(svc_id)
                warnings.append(
                    f"{svc_id}: post_install hook failed; service was not started",
                )
                continue

        pre_start_ok = _call_agent_hook(svc_id, "pre_start")
        if not pre_start_ok:
            results[svc_id] = "enabled_but_pre_start_failed"
            failed_services.append(svc_id)
            warnings.append(f"{svc_id}: pre_start hook failed; service was not started")
            continue

        start_ok, reason = _call_agent_result("start", svc_id)
        if not start_ok:
            if svc_id in library_installed:
                results[svc_id] = "library_installed_but_start_failed"
            else:
                results[svc_id] = "enabled_but_start_failed"
            failed_services.append(svc_id)
            warnings.append(f"{svc_id}: {reason or 'Host agent did not confirm startup; retry after checking the service.'}")
            continue

        post_start_ok = _call_agent_hook(svc_id, "post_start")
        if not post_start_ok:
            warnings.append(f"{svc_id}: post_start hook failed; manual configuration may be needed")

    skipped_services = [
        service_id
        for service_id, outcome in results.items()
        if isinstance(outcome, str) and outcome.startswith("skipped:")
    ]
    restart_required = bool(failed_services)

    return {
        "template_id": template["id"],
        "results": results,
        "enabled_count": len(enabled_services),
        "started_count": len(enabled_services) - len(failed_services),
        "library_installed": library_installed,
        "failed_services": failed_services,
        "skipped_services": skipped_services,
        "warnings": warnings,
        "restart_required": restart_required,
    }
