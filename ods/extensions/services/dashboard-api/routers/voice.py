"""Voice services status endpoint (stub)."""

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

    services_status = {}
    for svc_key, display_name in [("whisper", "stt"), ("tts", "tts")]:
        cfg = SERVICES.get(svc_key)
        if cfg:
            try:
                result = await check_service_health(svc_key, cfg)
                services_status[display_name] = {"status": result.status}
            except Exception:
                logger.warning("Health check failed for %s", svc_key, exc_info=True)
                services_status[display_name] = {"status": "unavailable"}
        else:
            services_status[display_name] = {"status": NOT_CONFIGURED}

    # LiveKit is optional and not in SERVICES by default
    livekit_cfg = SERVICES.get("livekit")
    if livekit_cfg:
        try:
            result = await check_service_health("livekit", livekit_cfg)
            services_status["livekit"] = {"status": result.status}
        except Exception:
            logger.warning("Health check failed for livekit", exc_info=True)
            services_status["livekit"] = {"status": "unavailable"}
    else:
        services_status["livekit"] = {"status": NOT_CONFIGURED}

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
