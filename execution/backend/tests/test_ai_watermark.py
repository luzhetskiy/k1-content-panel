import io

from PIL import Image, ImageChops

from app.ai.watermark import apply_watermark


def image_bytes(width, height, color, mode="RGB", fmt="PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (width, height), color).save(buffer, fmt)
    return buffer.getvalue()


def test_watermark_preserves_size():
    base = image_bytes(1600, 900, (10, 10, 10))
    mark = image_bytes(200, 80, (255, 255, 255, 200), mode="RGBA")
    result = apply_watermark(base, mark)
    assert Image.open(io.BytesIO(result)).size == (1600, 900)


def changed_box(base: bytes, marked: bytes):
    """Прямоугольник изменённых пикселей — где именно лёг знак."""
    before = Image.open(io.BytesIO(base)).convert("RGB")
    after = Image.open(io.BytesIO(marked)).convert("RGB")
    return ImageChops.difference(before, after).getbbox()


def test_watermark_lands_in_bottom_right_quadrant():
    base = image_bytes(1600, 900, (10, 10, 10))
    mark = image_bytes(200, 80, (255, 255, 255, 255), mode="RGBA")
    left, top, right, bottom = changed_box(base, apply_watermark(base, mark))
    assert left > 1600 / 2 and top > 900 / 2
    assert right <= 1600 and bottom <= 900


def test_watermark_is_small():
    """«Небольшой» — требование владельца, а не вкусовщина: знак помечает
    авторство, а не соперничает с содержимым кадра. Без этой проверки долю
    ширины можно молча увеличить, и никто не заметит."""
    base = image_bytes(1600, 900, (10, 10, 10))
    mark = image_bytes(200, 80, (255, 255, 255, 255), mode="RGBA")
    left, _, right, _ = changed_box(base, apply_watermark(base, mark))
    assert (right - left) <= 1600 * 0.15


def test_watermark_does_not_touch_edges():
    """Отступы от правого и нижнего краёв — тоже требование владельца."""
    base = image_bytes(1600, 900, (10, 10, 10))
    mark = image_bytes(200, 80, (255, 255, 255, 255), mode="RGBA")
    _, _, right, bottom = changed_box(base, apply_watermark(base, mark))
    assert right < 1600 - 1600 * 0.02
    assert bottom < 900 - 1600 * 0.02


def test_watermark_leaves_top_left_untouched():
    base = image_bytes(1600, 900, (10, 10, 10))
    mark = image_bytes(200, 80, (255, 255, 255, 255), mode="RGBA")
    after = Image.open(io.BytesIO(apply_watermark(base, mark))).convert("RGB")
    assert after.getpixel((20, 20)) == (10, 10, 10)


def test_oversized_watermark_is_scaled_down():
    """Знак шире картинки не должен обрезаться по краю — он масштабируется
    до доли ширины кадра."""
    base = image_bytes(800, 600, (10, 10, 10))
    mark = image_bytes(4000, 1000, (255, 255, 255, 255), mode="RGBA")
    result = apply_watermark(base, mark)
    assert Image.open(io.BytesIO(result)).size == (800, 600)


def test_empty_watermark_returns_original():
    base = image_bytes(800, 600, (10, 10, 10))
    assert apply_watermark(base, b"") == base
