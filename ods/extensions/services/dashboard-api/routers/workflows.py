"""Workflow management endpoints — n8n integration."""

import asyncio
import json
import logging
import re

import aiohttp
from fastapi import APIRouter, Depends, HTTPException

from config import (
    SERVICES, WORKFLOW_DIR, WORKFLOW_CATALOG_FILE,
    DEFAULT_WORKFLOW_CATALOG, N8N_URL, N8N_API_KEY,
)
from security import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["workflows"])
_WORKFLOW_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


# --- Helpers ---

def _validate_workflow_id(workflow_id: str) -> None:
    if not _WORKFLOW_ID_RE.fullmatch(workflow_id):
        raise HTTPException(status_code=400, detail="Invalid workflow ID format")


def load_workflow_catalog() -> dict:
    """Load workflow catalog from JSON file."""
    if not WORKFLOW_CATALOG_FILE.exists():
        return DEFAULT_WORKFLOW_CATALOG
    try:
        with open(WORKFLOW_CATALOG_FILE) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("Workflow catalog must be a JSON object: %s", WORKFLOW_CATALOG_FILE)
            return DEFAULT_WORKFLOW_CATALOG
        workflows = data.get("workflows", [])
        categories = data.get("categories", {})
        if not isinstance(workflows, list):
            workflows = []
        if not isinstance(categories, dict):
            categories = {}
        return {"workflows": workflows, "categories": categories}
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.warning("Failed to load workflow catalog from %s: %s", WORKFLOW_CATALOG_FILE, e)
        return DEFAULT_WORKFLOW_CATALOG


async def get_n8n_workflows() -> list[dict]:
    """Get all workflows from n8n API."""
    try:
        headers = {}
        if N8N_API_KEY:
            headers["X-N8N-API-KEY"] = N8N_API_KEY
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(f"{N8N_URL}/api/v1/workflows", headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", [])
    except (aiohttp.ClientError, OSError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to fetch workflows from n8n: {e}")
    return []


async def check_workflow_dependencies(deps: list[str], health_cache: dict[str, bool] | None = None) -> dict[str, bool]:
    """Check if required services are running. Uses health_cache to avoid duplicate checks."""
    from helpers import check_service_health

    _DEP_ALIASES = {"ollama": "llama-server"}
    if health_cache is None:
        health_cache = {}
    results = {}
    for dep in deps:
        resolved = _DEP_ALIASES.get(dep, dep)
        if resolved in health_cache:
            results[dep] = health_cache[resolved]
        elif resolved in SERVICES:
            status = await check_service_health(resolved, SERVICES[resolved])
            healthy = status.status == "healthy"
            health_cache[resolved] = healthy
            results[dep] = healthy
        else:
            results[dep] = True
    return results


async def check_n8n_available() -> bool:
    """Check if n8n is responding."""
    try:
        from helpers import _get_aio_session
        session = await _get_aio_session()
        async with session.get(f"{N8N_URL}/healthz") as resp:
            return resp.status < 500
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return False


# --- Endpoints ---

@router.get("/api/workflows/categories")
async def api_workflow_categories(api_key: str = Depends(verify_api_key)):
    """Return workflow categories from the catalog."""
    catalog = load_workflow_catalog()
    return {"categories": catalog.get("categories", {})}


@router.get("/api/workflows/n8n/status")
async def api_n8n_status(api_key: str = Depends(verify_api_key)):
    """Check n8n availability and return basic status."""
    available = await check_n8n_available()
    return {"available": available, "url": N8N_URL}


@router.get("/api/workflows")
async def api_workflows(api_key: str = Depends(verify_api_key)):
    """Get workflow catalog with status and dependency info."""
    catalog = load_workflow_catalog()
    n8n_workflows = await get_n8n_workflows()
    n8n_by_name = {w.get("name", "").lower(): w for w in n8n_workflows}

    workflows = []
    health_cache: dict[str, bool] = {}
    for wf in catalog.get("workflows", []):
        wf_name_lower = wf["name"].lower()
        installed = None
        for n8n_name, n8n_wf in n8n_by_name.items():
            if wf_name_lower in n8n_name or n8n_name in wf_name_lower:
                installed = n8n_wf
                break

        dep_status = await check_workflow_dependencies(wf.get("dependencies", []), health_cache)
        all_deps_met = all(dep_status.values())

        executions = 0
        if installed:
            executions = installed.get("statistics", {}).get("executions", {}).get("total", 0)

        workflows.append({
            "id": wf["id"],
            "name": wf["name"],
            "description": wf["description"],
            "icon": wf.get("icon", "Workflow"),
            "category": wf.get("category", "general"),
            "status": "active" if installed and installed.get("active") else ("installed" if installed else "available"),
            "installed": installed is not None,
            "active": installed.get("active", False) if installed else False,
            "n8nId": installed.get("id") if installed else None,
            "dependencies": wf.get("dependencies", []),
            "dependencyStatus": dep_status,
            "allDependenciesMet": all_deps_met,
            "diagram": wf.get("diagram", {}),
            "setupTime": wf.get("setupTime", "~2 min"),
            "executions": executions,
            "featured": wf.get("featured", False)
        })

    return {
        "workflows": workflows,
        "categories": catalog.get("categories", {}),
        "catalogSource": str(WORKFLOW_CATALOG_FILE),
        "workflowDir": str(WORKFLOW_DIR),
        "n8nUrl": N8N_URL,
        "n8nAvailable": len(n8n_workflows) > 0 or await check_n8n_available()
    }


@router.post("/api/workflows/{workflow_id}/enable")
async def enable_workflow(workflow_id: str, api_key: str = Depends(verify_api_key)):
    """Import a workflow template into n8n."""
    _validate_workflow_id(workflow_id)

    catalog = load_workflow_catalog()
    wf_info = next((wf for wf in catalog.get("workflows", []) if wf["id"] == workflow_id), None)
    if not wf_info:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

    dep_status = await check_workflow_dependencies(wf_info.get("dependencies", []))
    missing_deps = [dep for dep, ok in dep_status.items() if not ok]
    if missing_deps:
        raise HTTPException(status_code=400, detail=f"Missing dependencies: {', '.join(missing_deps)}. Enable these services first.")

    workflow_file = WORKFLOW_DIR / wf_info["file"]
    try:
        workflow_file = workflow_file.resolve()
        if not str(workflow_file).startswith(str(WORKFLOW_DIR.resolve())):
            raise HTTPException(status_code=400, detail="Invalid workflow file path")
    except HTTPException:
        raise
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid workflow file path")

    if not workflow_file.exists():
        raise HTTPException(status_code=404, detail=f"Workflow file not found: {wf_info['file']}")

    try:
        with open(workflow_file) as f:
            workflow_data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to read workflow: {e}")

    try:
        headers = {"Content-Type": "application/json"}
        if N8N_API_KEY:
            headers["X-N8N-API-KEY"] = N8N_API_KEY

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(f"{N8N_URL}/api/v1/workflows", headers=headers, json=workflow_data) as resp:
                if resp.status in (200, 201):
                    result = await resp.json()
                    n8n_id = result.get("data", {}).get("id")
                    activated = False
                    if n8n_id:
                        async with session.patch(f"{N8N_URL}/api/v1/workflows/{n8n_id}", headers=headers, json={"active": True}) as activate_resp:
                            activated = activate_resp.status == 200
                    return {"status": "success", "workflowId": workflow_id, "n8nId": n8n_id, "activated": activated, "message": f"{wf_info['name']} is now active!"}
                else:
                    error_text = await resp.text()
                    raise HTTPException(status_code=resp.status, detail=f"n8n API error: {error_text}")
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="n8n workflow add timed out")
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=503, detail=f"Cannot reach n8n: {e}")


async def _remove_workflow(workflow_id: str):
    """Shared logic for disabling/removing a workflow from n8n."""
    n8n_workflows = await get_n8n_workflows()
    catalog = load_workflow_catalog()
    wf_info = next((wf for wf in catalog.get("workflows", []) if wf["id"] == workflow_id), None)
    if not wf_info:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

    n8n_wf = None
    wf_name_lower = wf_info["name"].lower()
    for wf in n8n_workflows:
        if wf_name_lower in wf.get("name", "").lower():
            n8n_wf = wf
            break
    if not n8n_wf:
        raise HTTPException(status_code=404, detail="Workflow not installed in n8n")

    try:
        headers = {}
        if N8N_API_KEY:
            headers["X-N8N-API-KEY"] = N8N_API_KEY
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.delete(f"{N8N_URL}/api/v1/workflows/{n8n_wf['id']}", headers=headers) as resp:
                if resp.status in (200, 204):
                    return {"status": "success", "workflowId": workflow_id, "message": f"{wf_info['name']} has been removed"}
                else:
                    error_text = await resp.text()
                    raise HTTPException(status_code=resp.status, detail=f"n8n API error: {error_text}")
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="n8n workflow remove timed out")
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=503, detail=f"Cannot reach n8n: {e}")


@router.post("/api/workflows/{workflow_id}/disable")
async def disable_workflow_post(workflow_id: str, api_key: str = Depends(verify_api_key)):
    """Remove a workflow from n8n (POST /disable)."""
    _validate_workflow_id(workflow_id)
    return await _remove_workflow(workflow_id)


@router.delete("/api/workflows/{workflow_id}")
async def disable_workflow(workflow_id: str, api_key: str = Depends(verify_api_key)):
    """Remove a workflow from n8n (DELETE)."""
    _validate_workflow_id(workflow_id)
    return await _remove_workflow(workflow_id)


@router.get("/api/workflows/{workflow_id}/executions")
async def workflow_executions(workflow_id: str, limit: int = 20, api_key: str = Depends(verify_api_key)):
    """Get recent executions for a workflow."""
    _validate_workflow_id(workflow_id)
    n8n_workflows = await get_n8n_workflows()
    catalog = load_workflow_catalog()
    wf_info = next((wf for wf in catalog.get("workflows", []) if wf["id"] == workflow_id), None)
    if not wf_info:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

    n8n_wf = None
    wf_name_lower = wf_info["name"].lower()
    for wf in n8n_workflows:
        if wf_name_lower in wf.get("name", "").lower():
            n8n_wf = wf
            break
    if not n8n_wf:
        return {"executions": [], "message": "Workflow not installed"}

    try:
        headers = {}
        if N8N_API_KEY:
            headers["X-N8N-API-KEY"] = N8N_API_KEY
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(f"{N8N_URL}/api/v1/executions", headers=headers, params={"workflowId": n8n_wf["id"], "limit": limit}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {"workflowId": workflow_id, "n8nId": n8n_wf["id"], "executions": data.get("data", [])}
                else:
                    return {"executions": [], "error": "Failed to fetch executions"}
    except (aiohttp.ClientError, OSError, json.JSONDecodeError):
        logger.exception("Failed to fetch workflow executions")
        return {"executions": [], "error": "Failed to fetch executions"}
