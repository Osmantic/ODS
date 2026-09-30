"""Validate private Portal image input before persistence or model transport.

The original bytes are retained: this validator does not strip metadata, resize,
convert formats, or silently flatten animations. Storage and route capability
checks remain separate boundaries.
"""

from dataclasses import dataclass
import hashlib
from io import BytesIO
import warnings

from PIL import Image, UnidentifiedImageError


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_DIMENSION = 8192
SUPPORTED_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


class ImageInputError(ValueError):
    """Public, bounded error; never includes image bytes or decoder internals."""


@dataclass(frozen=True)
class ValidatedImage:
    data: bytes
    media_type: str
    width: int
    height: int
    sha256: str


def validate_image(data: bytes, media_type: str) -> ValidatedImage:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise ImageInputError("Choose a nonempty image no larger than 8 MiB.")
    if media_type not in SUPPORTED_FORMATS.values():
        raise ImageInputError("Choose a PNG, JPEG or WebP image.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if SUPPORTED_FORMATS.get(image.format) != media_type:
                    raise ImageInputError("The image content does not match its declared format.")
                width, height = image.size
                if (width < 1 or height < 1 or max(width, height) > MAX_IMAGE_DIMENSION
                        or width * height > MAX_IMAGE_PIXELS):
                    raise ImageInputError("The image exceeds the supported dimensions or pixel limit.")
                if getattr(image, "n_frames", 1) != 1:
                    raise ImageInputError("Animated images are not supported; choose a still image.")
                image.verify()
            # Verify checks structure; load also requires complete decodable pixels.
            with Image.open(BytesIO(data)) as image:
                image.load()
    except ImageInputError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ImageInputError("The image is invalid or cannot be decoded safely.") from exc
    return ValidatedImage(data, media_type, width, height, hashlib.sha256(data).hexdigest())
