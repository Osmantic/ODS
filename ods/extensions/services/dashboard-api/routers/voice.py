"""Voice services status endpoint (stub)."""

import logging

from fastapi import APIRouter, Depends

from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])


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
                logger.warning("Health check failed for %s", svc_key)
                services_status[display_name] = {"status": "unavailable"}
        else:
            services_status[display_name] = {"status": "not_configured"}

    # LiveKit is optional and not in SERVICES by default
    livekit_cfg = SERVICES.get("livekit")
    if livekit_cfg:
        try:
            result = await check_service_health("livekit", livekit_cfg)
            services_status["livekit"] = {"status": result.status}
        except Exception:
            logger.warning("Health check failed for livekit")
            services_status["livekit"] = {"status": "unavailable"}
    else:
        services_status["livekit"] = {"status": "not_configured"}

    all_healthy = all(s.get("status") == "healthy" for s in services_status.values())

    return {
        "available": all_healthy,
        "services": services_status,
        "message": "All voice services operational" if all_healthy else "Some voice services unavailable",
    }
