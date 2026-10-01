"""Resolve scoped image references without trusting browser bytes or URLs.

This prepares provider content only; it does not establish model capability or
deliver a turn. Callers must retain references for history/idempotency and must
verify the active route before dispatching these multimodal parts.
"""

import base64
import hmac

from pydantic import BaseModel, ConfigDict, Field

from pixel_image_input import MAX_IMAGE_BYTES


MAX_TURN_IMAGES = 4
MAX_TURN_IMAGE_BYTES = MAX_IMAGE_BYTES


class ImageReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=36, max_length=36, pattern=r"^img-[a-f0-9]{32}$")
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")


class ImageResolutionError(ValueError):
    """No partial/image-less request should be dispatched on this error."""


def resolve_image_parts(store, owner, chat, text, references):
    if not isinstance(text, str):
        raise ImageResolutionError("Image message text must be a string")
    if not isinstance(references, list) or not 1 <= len(references) <= MAX_TURN_IMAGES:
        raise ImageResolutionError("Attach between one and four images per message")
    normalized = [ImageReference.model_validate(value) for value in references]
    if len({value.id for value in normalized}) != len(normalized):
        raise ImageResolutionError("The same image was attached more than once")
    images = []
    total = 0
    for reference in normalized:
        image = store.get(owner, chat, reference.id)
        if image is None:
            raise ImageResolutionError("A conversation image is unavailable; attach it again before sending")
        if not hmac.compare_digest(image["sha256"], reference.sha256):
            raise ImageResolutionError("The conversation image no longer matches its recorded identity")
        total += len(image["data"])
        if total > MAX_TURN_IMAGE_BYTES:
            raise ImageResolutionError("Attached images exceed the 8 MiB combined message limit")
        images.append(image)
    # Resolve and validate every reference before building any provider payload.
    store.retain(owner, chat, [value.model_dump() for value in normalized])
    parts = [{"type": "text", "text": text}] if text else []
    parts.extend({"type": "image_url", "image_url": {
        "url": "data:" + image["media_type"] + ";base64," + base64.b64encode(image["data"]).decode("ascii"),
    }} for image in images)
    return parts
