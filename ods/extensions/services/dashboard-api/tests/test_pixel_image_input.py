from io import BytesIO

import pytest
from PIL import Image

from pixel_image_input import ImageInputError, validate_image


def encoded(fmt="PNG", size=(12, 9)):
    out = BytesIO()
    Image.new("RGB", size, "#237cab").save(out, format=fmt)
    return out.getvalue()


@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")])
def test_actual_pixels_and_original_bytes_preserved(fmt, mime):
    data = encoded(fmt)
    result = validate_image(data, mime)
    assert result.data == data
    assert result.media_type == mime
    assert (result.width, result.height) == (12, 9)
    assert len(result.sha256) == 64


@pytest.mark.parametrize("data,mime", [(b"", "image/png"), (b"<svg/>", "image/svg+xml"),
    (b"not an image", "image/png"), (encoded(), "image/jpeg"), (encoded()[:40], "image/png")])
def test_invalid_or_misdeclared_content_rejected(data, mime):
    with pytest.raises(ImageInputError):
        validate_image(data, mime)


def test_long_narrow_image_is_bounded_before_pixel_decode():
    with pytest.raises(ImageInputError, match="dimensions"):
        validate_image(encoded(size=(8193, 1)), "image/png")


def test_animation_not_silently_flattened():
    out = BytesIO()
    Image.new("RGB", (8, 8), "red").save(out, format="PNG", save_all=True,
        append_images=[Image.new("RGB", (8, 8), "blue")], duration=100, loop=0)
    with pytest.raises(ImageInputError, match="Animated"):
        validate_image(out.getvalue(), "image/png")


def test_encoded_byte_limit():
    with pytest.raises(ImageInputError, match="8 MiB"):
        validate_image(b"x" * (8 * 1024 * 1024 + 1), "image/png")
