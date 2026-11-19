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
# даже если max_retries > 1. Исключение — error-конверт RouterAI внутри 200
# (тесты в конце файла): transient-коды ретраятся как HTTP-сбои, остальные —
# немедленный отказ, но всегда с сообщением провайдера, а не KeyError.

def _post_once_returning(monkeypatch, payload, text=""):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return FakeResponse(200, payload, text=text)

    monkeypatch.setattr("app.ai.images.requests.post", fake_post)
    monkeypatch.setattr("app.ai.images.time.sleep", lambda _s: None)
    return calls


def test_generate_wraps_missing_data_key_as_image_error(monkeypatch):
    calls = _post_once_returning(monkeypatch, {"usage": {"cost": 1.0}},
                                 text='{"usage": {"cost": 1.0}}')
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError, match="разобрать") as ei:
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1
    # Фрагмент тела в сообщении — иначе причина невидима (инцидент 2026-11-19:
    # KeyError 'data' без тела не сказал ничего про авар upstream-модели).
    assert '{"usage"' in str(ei.value)


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


# Error-конверт RouterAI внутри HTTP 200. Реальный инцидент 2026-11-19 (партия
# 171): upstream модели отдавал 404, RouterAI слал 200 с телом
# {"error": {"message": "Провайдер вернул пустой ответ (HTTP 404). Попробуйте
# ещё раз.", "code": 503, "metadata": {"provider_error_code": "404"}}}.
# response.ok при этом true, и старый код падал KeyError 'data', теряя и
# сообщение провайдера, и возможность повторить transient-сбой.

@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_generate_retries_transient_error_envelope_in_200(monkeypatch, code):
    payload = {"error": {
        "message": "Провайдер вернул пустой ответ (HTTP 404). Попробуйте ещё раз.",
        "code": code, "metadata": {"provider_error_code": "404"}}}
    calls = _post_once_returning(monkeypatch, payload)
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError) as ei:
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 3
    assert "Провайдер вернул пустой ответ" in str(ei.value)
    assert str(code) in str(ei.value)
    assert "генерация не удалась после 3 попыток" in str(ei.value)


@pytest.mark.parametrize("code", [400, 401, 404])
def test_generate_non_retryable_error_envelope_fails_immediately(monkeypatch, code):
    payload = {"error": {"message": "модель не найдена", "code": code}}
    calls = _post_once_returning(monkeypatch, payload)
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError) as ei:
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1
    assert "модель не найдена" in str(ei.value)


def test_generate_transient_envelope_then_success(monkeypatch):
    envelope = {"error": {"message": "временный сбой", "code": 503}}
    success = {
        "data": [{"b64_json": base64.b64encode(png_bytes(1536, 1024)).decode()}],
        "usage": {"cost": 5.4},
    }
    responses = [FakeResponse(200, envelope), FakeResponse(200, success)]
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return responses[len(calls) - 1]

    monkeypatch.setattr("app.ai.images.requests.post", fake_post)
    monkeypatch.setattr("app.ai.images.time.sleep", lambda _s: None)
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=2)
    result = generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 2
    assert result.cost == 5.4
    assert Image.open(io.BytesIO(result.data)).format == "WEBP"


@pytest.mark.parametrize("payload", [
    {"error": {"message": "что-то сломалось"}},
    {"error": "просто строка"},
])
def test_generate_envelope_without_transient_code_fails_immediately(monkeypatch, payload):
    calls = _post_once_returning(monkeypatch, payload)
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError) as ei:
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1
    error = payload["error"]
    expected = error["message"] if isinstance(error, dict) else error
    assert expected in str(ei.value)


def test_generate_envelope_string_code_is_coerced(monkeypatch):
    payload = {"error": {"message": "временный сбой", "code": "503"}}
    calls = _post_once_returning(monkeypatch, payload)
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=2)
    with pytest.raises(ImageError):
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 2


class FakeNonJsonResponse(FakeResponse):
    def json(self):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


def test_generate_non_json_body_fails_immediately_with_snippet(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return FakeNonJsonResponse(200, text="<html>bad gateway</html>")

    monkeypatch.setattr("app.ai.images.requests.post", fake_post)
    monkeypatch.setattr("app.ai.images.time.sleep", lambda _s: None)
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError) as ei:
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1
    assert "не JSON" in str(ei.value)
    assert "Expecting value" in str(ei.value)
    assert "<html>bad gateway</html>" in str(ei.value)


def test_generate_envelope_message_is_capped(monkeypatch):
    long_message = "очень длинное сообщение провайдера " * 20
    payload = {"error": {"message": long_message, "code": 400}}
    calls = _post_once_returning(monkeypatch, payload)
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError) as ei:
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1
    # Сообщение провайдера ограничено 200 символами — как и фрагменты тела
    # в остальных ошибках: error_text не должен раздуваться на весь UI.
    assert long_message[:200] in str(ei.value)
    assert long_message[:201] not in str(ei.value)


def test_generate_success_body_with_null_error_returns_image(monkeypatch):
    payload = {
        "error": None,
        "data": [{"b64_json": base64.b64encode(png_bytes(1536, 1024)).decode()}],
        "usage": {"cost": 5.4},
    }
    calls = _post_once_returning(monkeypatch, payload)
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2")
    result = generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1
    assert result.cost == 5.4
    assert Image.open(io.BytesIO(result.data)).format == "WEBP"


def test_generate_envelope_wins_over_data(monkeypatch):
    # Приоритет конверта над data — намеренный: успешные тела RouterAI ключа
    # error не содержат, гибрид «картинка + ошибка» считаем отказом провайдера.
    payload = {
        "error": {"message": "отказ", "code": 400},
        "data": [{"b64_json": base64.b64encode(png_bytes(64, 64)).decode()}],
    }
    calls = _post_once_returning(monkeypatch, payload)
    generator = ImageGenerator("https://routerai.ru/api/v1", "key", "openai/gpt-image-2",
                               max_retries=3)
    with pytest.raises(ImageError, match="отказ"):
        generator.generate(prompt="дом", size="1536x1024", quality="medium", crop=None)
    assert len(calls) == 1
