"""Наложение водяного знака сайта на контентные картинки.

Знак ставится в правый нижний угол, небольшой, с отступом от обоих краёв;
на обложку статьи знак НЕ накладывается — это витрина, а не иллюстрация
внутри текста.

Пропорции заданы владельцем по образцу готовой картинки: знак занимает
примерно одну десятую ширины кадра и не касается краёв. Это осознанно
скромно — знак помечает авторство, а не борется за внимание с содержимым
кадра. Числа ниже закреплены тестами (`tests/test_ai_watermark.py`):
проверяется и доля ширины, и наличие отступов, поэтому «чуть покрупнее»
не пройдёт молча.
"""

from __future__ import annotations

import io

from PIL import Image

MARK_WIDTH_FRACTION = 0.11   # доля ширины кадра, которую занимает знак
MARGIN_FRACTION = 0.045      # отступ от краёв, доля ширины кадра
OPACITY = 0.75


def apply_watermark(image_bytes: bytes, watermark_bytes: bytes) -> bytes:
    """Возвращает webp с наложенным знаком. Пустой знак — картинка без изменений."""
    if not watermark_bytes:
        return image_bytes

    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    mark = Image.open(io.BytesIO(watermark_bytes)).convert("RGBA")

    # Знак масштабируется по ширине кадра, а не берётся как есть: файлы знаков
    # у разных сайтов разного размера, и знак шире картинки обрезался бы краем.
    target_width = max(1, int(base.width * MARK_WIDTH_FRACTION))
    target_height = max(1, round(mark.height * target_width / mark.width))
    mark = mark.resize((target_width, target_height), Image.LANCZOS)

    if OPACITY < 1.0:
        alpha = mark.getchannel("A").point(lambda v: int(v * OPACITY))
        mark.putalpha(alpha)

    margin = int(base.width * MARGIN_FRACTION)
    position = (base.width - target_width - margin, base.height - target_height - margin)

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(mark, position)
    merged = Image.alpha_composite(base, layer).convert("RGB")

    buffer = io.BytesIO()
    merged.save(buffer, "WEBP", quality=82, method=6)
    return buffer.getvalue()
