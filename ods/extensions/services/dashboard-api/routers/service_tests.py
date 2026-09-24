"""Post-install feature probes for the dashboard's Success Validation screen.

The dashboard's SuccessValidation component and ods/tests/test-phase-c-p1.sh
both expect GET /api/test/{llm,voice,rag,workflows}. None of those routes
existed, so every "Run live tests" click fetched a 404, JSON-decoded the error
page, and reported the feature as failed on an install that was in fact
healthy (#4174).

Each probe reports on the services that feature actually needs, reusing the
same health infrastructure as /api/voice/status rather than inventing a second
notion of "is this up".

The response carries BOTH `success` and `available`: the dashboard reads
`result.success ?? result.available`, and /api/voice/status already returns
`available`, so emitting both keeps one shape across every probe.
"""

import logging

from fastapi import APIRouter, Depends

from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["service-tests"])

NOT_CONFIGURED = "not_configured"
UNAVAILABLE = "unavailable"

# Which services each feature genuinely needs. Keys are manifest service ids.
#
# "llm" maps to llama-server for every backend: the Lemonade, host-native AMD
# and external-provider paths all *repoint* that entry's host/port/health in
# config.py rather than removing it, so it stays the right thing to probe. It
# is absent only when no local inference service is installed at all (e.g.
# cloud mode), which is correctly reported as not configured.
FEATURE_SERVICES: dict[str, tuple[str, ...]] = {
    "llm": ("llama-server",),
    "voice": ("whisper", "tts"),
    "rag": ("qdrant",),
    "workflows": ("n8n",),
}


async def _probe_feature(feature: str) -> dict:
    """Health-check every service `feature` needs and summarise the verdict."""
    from config import SERVICES
    from helpers import check_service_health

    services: dict[str, str] = {}
    for service_id in FEATURE_SERVICES[feature]:
        config = SERVICES.get(service_id)
        if not config:
            services[service_id] = NOT_CONFIGURED
            continue
        try:
            result = await check_service_health(service_id, config)
            services[service_id] = result.status
        except (OSError, TimeoutError):
            # A probe that cannot reach the service is a real answer, not a
            # crash: the feature is unusable and the operator needs to see so.
            logger.warning("Health check failed for %s", service_id, exc_info=True)
            services[service_id] = UNAVAILABLE

    success = bool(services) and all(status == "healthy" for status in services.values())

    error = None
    if not success:
        missing = [sid for sid, status in services.items() if status == NOT_CONFIGURED]
        unhealthy = [
            f"{sid} ({status})"
            for sid, status in services.items()
            if status not in ("healthy", NOT_CONFIGURED)
        ]
        parts = []
        if missing:
            parts.append(f"not installed: {', '.join(missing)}")
        if unhealthy:
            parts.append(f"not healthy: {', '.join(unhealthy)}")
        error = "; ".join(parts) or "feature unavailable"

    return {
        "success": success,
        # The dashboard falls back to `available`; /api/voice/status uses it.
        "available": success,
        "feature": feature,
        "services": services,
        "error": error,
    }


@router.get("/api/test/llm")
async def test_llm(api_key: str = Depends(verify_api_key)):
    """Is local LLM inference usable?"""
    return await _probe_feature("llm")


@router.get("/api/test/voice")
async def test_voice(api_key: str = Depends(verify_api_key)):
    """Are speech-to-text and text-to-speech both usable?"""
    return await _probe_feature("voice")


@router.get("/api/test/rag")
async def test_rag(api_key: str = Depends(verify_api_key)):
    """Is the vector store backing document chat usable?"""
    return await _probe_feature("rag")


@router.get("/api/test/workflows")
async def test_workflows(api_key: str = Depends(verify_api_key)):
    """Is the workflow engine usable?"""
    return await _probe_feature("workflows")
