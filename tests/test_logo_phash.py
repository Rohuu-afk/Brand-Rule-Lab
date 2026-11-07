import io
import random

from PIL import Image, ImageDraw, ImageEnhance

from engines.rule_engine import run_rule


def _image_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _base_image() -> Image.Image:
    image = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, 55, 55), fill="black")
    draw.ellipse((20, 20, 43, 43), fill="white")
    draw.rectangle((29, 24, 34, 39), fill="black")
    return image


def _phash(image: Image.Image) -> str:
    import imagehash

    return str(imagehash.phash(image))


def _compare(submitted: bytes | None, approved: str | None = None):
    return run_rule(
        {
            "rule_type": "logo_phash",
            "is_active": 1,
            "owner_approved": 1,
            "parameters": {
                "submitted_image_bytes": submitted,
                "approved_phash": approved or _phash(_base_image()),
            },
        },
        "",
    )


def test_identical_image_passes_with_distance_and_limitation():
    image = _base_image()
    result = _compare(_image_bytes(image), _phash(image))

    assert result.result.value == "PASS"
    assert "distance 0" in result.explanation
    assert "threshold 10" in result.explanation
    assert "does not locate a logo inside a full ad" in result.explanation


def test_resized_copy_passes():
    image = _base_image()
    resized = image.resize((128, 128))

    assert _compare(_image_bytes(resized), _phash(image)).result.value == "PASS"


def test_slightly_altered_copy_passes():
    image = ImageEnhance.Brightness(_base_image()).enhance(0.85)

    assert _compare(_image_bytes(image)).result.value == "PASS"


def test_completely_different_image_fails():
    image = Image.new("RGB", (64, 64))
    generator = random.Random(137)
    for y in range(image.height):
        for x in range(image.width):
            shade = generator.choice((0, 255))
            image.putpixel((x, y), (shade, shade, shade))

    result = _compare(_image_bytes(image))

    assert result.result.value == "FAIL"
    assert "distance" in result.explanation
    assert "threshold 10" in result.explanation
    assert "looks different" in result.explanation


def test_missing_reference_is_not_observed():
    result = run_rule(
        {
            "rule_type": "logo_phash",
            "is_active": 1,
            "owner_approved": 1,
            "parameters": {"submitted_image_bytes": _image_bytes(_base_image())},
        },
        "",
    )

    assert result.result.value == "NOT_OBSERVED"


def test_corrupt_image_bytes_are_not_observed():
    result = _compare(b"not an image")

    assert result.result.value == "NOT_OBSERVED"
