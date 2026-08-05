import base64
import io

import pytest
from PIL import Image

from app.ai.images import ImageError, ImageGenerator, crop_to_ratio, to_webp


def png_bytes(width: int, height: int, color=(120, 140, 160)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


def test_crop_wide_image_to_square():
    cropped = crop_to_ratio(Image.new("RGB", (1536, 1024)), "1:1")
    assert cropped.size == (1024, 1024)


def test_crop_tall_image_to_wide():
    cropped = crop_to_ratio(Image.new("RGB", (1024, 1536)), "21:9")
    assert cropped.size == (1024, 438)


def test_crop_keeps_already_correct_ratio():
    cropped = crop_to_ratio(Image.new("RGB", (1536, 1024)), "3:2")
    assert cropped.size == (1536, 1024)


def test_to_webp_downscales_to_max_width():
    data, size = to_webp(png_bytes(2400, 1600), crop=None)
    assert size == (1600, 1066)
    assert Image.open(io.BytesIO(data)).format == "WEBP"


def test_to_webp_applies_crop_before_resize():
    _, size = to_webp(png_bytes(1536, 1024), crop="1:1")
    assert size == (1024, 1024)


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def test_generate_returns_webp_and_cost(monkeypatch):
    payload = {
        "data": [{"b64_json": base64.b64encode(png_bytes(1536, 1024)).decode()}],
        "usage": {"cost": 5.4},
    }
    monkeypatch.setattr("app.ai.images.requests.post",
                        lambda *a, **kw: FakeResponse(200, payload))
    result = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2").generate(
        prompt="дом в лесу", size="1536x1024", quality="medium", crop="3:2")
    assert result.cost == 5.4
    assert Image.open(io.BytesIO(result.data)).format == "WEBP"


def test_generate_retries_then_fails(monkeypatch):
    calls = []

    def always_500(*args, **kwargs):
        calls.append(1)
        return FakeResponse(500, text="upstream error")

    monkeypatch.setattr("app.ai.images.requests.post", always_500)
    monkeypatch.setattr("app.ai.images.time.sleep", lambda _s: None)
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError, match="500"):
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 3


# «200 OK, но тело не разобрать» — не лечится повтором (тот же провайдер с
# высокой вероятностью вернёт тот же мусор), поэтому каждый из следующих
# случаев обязан обернуться в ImageError и НЕ дёргать requests.post повторно,
# даже если max_retries > 1.

def _post_once_returning(monkeypatch, payload):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return FakeResponse(200, payload)

    monkeypatch.setattr("app.ai.images.requests.post", fake_post)
    monkeypatch.setattr("app.ai.images.time.sleep", lambda _s: None)
    return calls


def test_generate_wraps_missing_data_key_as_image_error(monkeypatch):
    calls = _post_once_returning(monkeypatch, {"usage": {"cost": 1.0}})
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError, match="разобрать"):
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1


def test_generate_wraps_non_image_payload_as_image_error(monkeypatch):
    garbage_b64 = base64.b64encode(b"this is not an image at all").decode()
    calls = _post_once_returning(
        monkeypatch, {"data": [{"b64_json": garbage_b64}], "usage": {"cost": 1.0}})
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError, match="разобрать"):
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1


def test_generate_wraps_invalid_base64_as_image_error(monkeypatch):
    calls = _post_once_returning(
        monkeypatch, {"data": [{"b64_json": "!!!not-base64!!!"}], "usage": {"cost": 1.0}})
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError, match="разобрать"):
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1


def test_generate_wraps_truncated_image_as_image_error(monkeypatch):
    full_png = png_bytes(800, 600)
    truncated_b64 = base64.b64encode(full_png[: len(full_png) // 2]).decode()
    calls = _post_once_returning(
        monkeypatch, {"data": [{"b64_json": truncated_b64}], "usage": {"cost": 1.0}})
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError, match="разобрать"):
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1
