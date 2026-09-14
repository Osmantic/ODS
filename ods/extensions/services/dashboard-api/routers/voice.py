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

    Returns service health based on the existing service health infrastructure.
    """
    from helpers import check_service_health
    from config import SERVICES

    services_status = {}
    for svc_key, display_name in [("whisper", "stt"), ("tts", "tts")]:
        cfg = SERVICES.get(svc_key)
        if cfg and isinstance(cfg, dict):
            try:
                result = await check_service_health(svc_key, cfg)
                status_str = getattr(result, "status", "unknown")
                services_status[display_name] = {"status": status_str, "configured": True}
            except Exception as exc:
                logger.warning("Voice service health check failed for %s: %s", svc_key, exc, exc_info=True)
                services_status[display_name] = {"status": "unavailable", "configured": True}
        else:
            services_status[display_name] = {"status": NOT_CONFIGURED, "configured": False}

    # LiveKit is optional and not in SERVICES by default
    livekit_cfg = SERVICES.get("livekit")
    if livekit_cfg and isinstance(livekit_cfg, dict):
        try:
            result = await check_service_health("livekit", livekit_cfg)
            status_str = getattr(result, "status", "unknown")
            services_status["livekit"] = {"status": status_str, "configured": True}
        except Exception as exc:
            logger.warning("Voice service health check failed for livekit: %s", exc, exc_info=True)
            services_status["livekit"] = {"status": "unavailable", "configured": True}
    else:
        services_status["livekit"] = {"status": NOT_CONFIGURED, "configured": False}

    # An uninstalled optional service is not a failure, so it sits out the
    # verdict. Everything that IS installed still has to be healthy.
    required_healthy = all(
        isinstance(services_status.get(name), dict) and services_status.get(name, {}).get("status") == "healthy"
        for name in REQUIRED_VOICE_SERVICES
    )
    installed_healthy = all(
        entry.get("status") == "healthy"
        for entry in services_status.values()
        if isinstance(entry, dict) and entry.get("status") != NOT_CONFIGURED
    )
    all_healthy = required_healthy and installed_healthy

    return {
        "available": all_healthy,
        "services": services_status,
        "message": "All voice services operational" if all_healthy else "Some voice services unavailable",
    }
