"""Генерация иллюстраций через RouterAI.

Модель gpt-image-2 отдаёт только фиксированные размеры (1024x1024, 1536x1024,
1024x1536) и игнорирует aspect_ratio — нужные пропорции получаем центральным
кропом. Порт execution/articles/gen_images.py: манифест и .env заменены
параметрами вызова, параллелизм поднят на уровень Celery-задачи.
"""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass

import requests
from PIL import Image

MAX_WIDTH = 1600
WEBP_QUALITY = 82
# Один кадр в норме укладывается в 40-140 с (см. execution/articles/
# gen_images.py и практику), 180 с — щедрый запас под одну попытку. Худший
# случай всей generate() с max_retries=2: 180×2 + backoff(5×1) между ними =
# 365 с — та же величина, что и у худшего случая текстового вызова
# (app/ai/text.py: REQUEST_TIMEOUT_SECONDS=120 × 3 попытки + паузы ≈366 с).
# Раньше TIMEOUT=420 и max_retries=3 давали до ≈1275 с на одну картинку —
# втрое больше всего бюджета статьи (ARTICLE_TIME_BUDGET_SECONDS в Task 18).
TIMEOUT = 180


class ImageError(RuntimeError):
    pass


@dataclass
class ImageResult:
    data: bytes
    size: tuple[int, int]
    cost: float
    seconds: int


def crop_to_ratio(image: Image.Image, ratio: str) -> Image.Image:
    """Центральный кроп до заданного соотношения сторон ('21:9')."""
    rw, rh = (int(x) for x in ratio.split(":"))
    target = rw / rh
    width, height = image.size
    if width / height > target:
        new_width = int(height * target)
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))
    new_height = int(width / target)
    top = (height - new_height) // 2
    return image.crop((0, top, width, top + new_height))


def to_webp(raw: bytes, crop: str | None) -> tuple[bytes, tuple[int, int]]:
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    if crop:
        image = crop_to_ratio(image, crop)
    if image.width > MAX_WIDTH:
        # int(), а не round(): та же функция округления, что и в
        # crop_to_ratio выше, — единое правило на модуль, чтобы никто не
        # «поправил» одну из них по вкусу и не рассинхронизировал результат.
        image = image.resize((MAX_WIDTH, int(image.height * MAX_WIDTH / image.width)),
                             Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=WEBP_QUALITY, method=6)
    return buffer.getvalue(), image.size


class ImageGenerator:
    def __init__(self, base_url: str, api_key: str, model: str, max_retries: int = 2,
                 backoff: float = 5.0):
        self.url = base_url.rstrip("/") + "/images"
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries
        self.backoff = backoff

    def generate(self, prompt: str, size: str, quality: str,
                 crop: str | None) -> ImageResult:
        payload = {
            "model": self.model, "prompt": prompt, "n": 1,
            "size": size, "quality": quality, "output_format": "webp",
        }
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}

        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            started = time.time()
            try:
                response = requests.post(self.url, headers=headers, json=payload,
                                         timeout=TIMEOUT)
            except Exception as exc:
                last_error = str(exc)[:200]
            else:
                if response.ok:
                    try:
                        body = response.json()
                        raw = base64.b64decode(body["data"][0]["b64_json"])
                        data, image_size = to_webp(raw, crop)
                    except Exception as exc:
                        # Мусор в теле 200-го ответа не лечится повтором — тот
                        # же аргумент, что и с неретраибельными ошибками в
                        # app/ai/text.py: повторный запрос с высокой
                        # вероятностью вернёт тот же мусор, а не другой ответ.
                        raise ImageError(
                            f"RouterAI images вернул 200, но тело не "
                            f"разобрать ({type(exc).__name__}): {exc}") from exc
                    return ImageResult(
                        data=data, size=image_size,
                        cost=float(body.get("usage", {}).get("cost") or 0.0),
                        seconds=round(time.time() - started),
                    )
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            if attempt < self.max_retries:
                time.sleep(self.backoff * attempt)

        raise ImageError(f"RouterAI images не ответил после {self.max_retries} попыток: "
                         f"{last_error}")
