"""Voice services status endpoint (stub)."""

import asyncio
import logging

from fastapi import APIRouter, Depends

from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])

# Whisper (STT) and Kokoro (TTS) are the voice stack ODS ships, so both have to
# be healthy for voice to be usable. LiveKit is an optional add-on that is
# absent from SERVICES unless an operator installs it — an install without it
# is complete, not degraded, and must not drag the verdict down.
REQUIRED_VOICE_SERVICES = ("stt", "tts")
NOT_CONFIGURED = "not_configured"


@router.get("/api/voice/status")
async def voice_status(api_key: str = Depends(verify_api_key)):
    """Return voice services availability status.

    Stub implementation — returns service health based on the existing
    service health infrastructure. Full voice API is not yet implemented.
    """
    from helpers import check_service_health
    from config import SERVICES

    voice_services = [("whisper", "stt"), ("tts", "tts"), ("livekit", "livekit")]
    services_status = {name: {"status": NOT_CONFIGURED} for _, name in voice_services}
    configured = [(key, name, SERVICES[key]) for key, name in voice_services if SERVICES.get(key)]
    results = await asyncio.gather(
        *(check_service_health(key, cfg) for key, _, cfg in configured),
        return_exceptions=True,
    )
    for (key, name, _), result in zip(configured, results):
        if isinstance(result, Exception):
            # Preserve the existing per-service error verdict and diagnostic.
            logger.warning("Health check failed for %s", key, exc_info=(type(result), result, result.__traceback__))
            services_status[name] = {"status": "unavailable"}
        elif isinstance(result, BaseException):
            raise result
        else:
            services_status[name] = {"status": result.status}

    # An uninstalled optional service is not a failure, so it sits out the
    # verdict. Everything that IS installed still has to be healthy.
    required_healthy = all(
        services_status.get(name, {}).get("status") == "healthy"
        for name in REQUIRED_VOICE_SERVICES
    )
    installed_healthy = all(
        entry["status"] == "healthy"
        for entry in services_status.values()
        if entry["status"] != NOT_CONFIGURED
    )
    all_healthy = required_healthy and installed_healthy

    return {
        "available": all_healthy,
        "services": services_status,
        "message": "All voice services operational" if all_healthy else "Some voice services unavailable",
    }
