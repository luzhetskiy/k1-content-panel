"""Проверка рерайта описания проекта против оригинала.

Факты о проекте код сверяет по числам — их можно проверить надёжно: площади,
размеры, этажность. Материалы и конструктив («монолитная плита») держатся на
запрете в промпте. Нарушения уходят модели списком на второй попытке."""

from __future__ import annotations

import re

from app.house_projects.markup import plain_text

VOLUME_TOLERANCE = 0.10      # владелец: объём ±5–10 %
SHINGLE_WORDS = 4
MAX_SHARED_SHINGLES = 0.25   # доля 4-словных цепочек оригинала, допустимая в рерайте

_NUMBER = re.compile(r"[0-9]+(?:[.,][0-9]+)?")
_TAG_NAME = re.compile(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)")
_LIST = re.compile(r"<(ul|ol)\b[^>]*>(.*?)</\1\s*>", re.DOTALL | re.IGNORECASE)
_WORD = re.compile(r"\w+")


def number_spellings(fragment: str) -> dict[str, str]:
    """Число → как оно впервые написано в тексте: подсказка модели на повтор
    должна повторять написание оригинала («21,17», а не «21.17»)."""
    spellings: dict[str, str] = {}
    for value in _NUMBER.findall(plain_text(fragment)):
        spellings.setdefault(value.replace(",", "."), value)
    return spellings


def numbers(fragment: str) -> set[str]:
    """«82.64» и «82,64» — одно число; «11×9» — два."""
    return set(number_spellings(fragment))


def tag_names(fragment: str) -> set[str]:
    return {name.lower() for name in _TAG_NAME.findall(fragment)}


def heading_count(fragment: str) -> int:
    return len(re.findall(r"<h3\b", fragment, re.IGNORECASE))


def list_sizes(fragment: str) -> list[int]:
    return [len(re.findall(r"<li\b", body, re.IGNORECASE)) for _, body in _LIST.findall(fragment)]


def shared_shingles(original: str, rewritten: str) -> float:
    """Доля цепочек из SHINGLE_WORDS слов оригинала, повторённых в рерайте."""
    def shingles(fragment: str) -> set[tuple[str, ...]]:
        words = [w.casefold() for w in _WORD.findall(plain_text(fragment))]
        return {tuple(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}

    source = shingles(original)
    if not source:
        return 0.0
    return len(source & shingles(rewritten)) / len(source)


def volume_range(original: str) -> tuple[int, int]:
    size = len(plain_text(original))
    return round(size * (1 - VOLUME_TOLERANCE)), round(size * (1 + VOLUME_TOLERANCE))


def validate_rewrite(original: str, rewritten: str) -> list[str]:
    errors: list[str] = []
    stripped = rewritten.strip()
    if not (stripped.startswith("<") and stripped.endswith(">")):
        errors.append("ответ — не чистый HTML: верни только разметку описания, "
                      "без пояснений до и после")
    low, high = volume_range(original)
    size = len(plain_text(rewritten))
    if not low <= size <= high:
        errors.append(f"объём {size} символов без тегов, нужно {low}–{high}")
    source, target = number_spellings(original), number_spellings(rewritten)
    missing = sorted(set(source) - set(target), key=float)
    if missing:
        errors.append(f"пропали числа из оригинала: {', '.join(source[v] for v in missing)} "
                      f"— верни их как были")
    # Однозначные числа — пересказ («в 2 уровня» вместо «двухуровневая»), а от
    # двух цифр и дробные — уже новый факт: «служит 100 лет», лишняя площадь.
    added = sorted((v for v in set(target) - set(source) if len(v) > 1), key=float)
    if added:
        errors.append(f"числа, которых нет в оригинале: {', '.join(target[v] for v in added)} "
                      f"— не добавляй новых сведений")
    if heading_count(rewritten) != heading_count(original):
        errors.append(f"подзаголовков <h3> {heading_count(rewritten)}, "
                      f"в оригинале {heading_count(original)}")
    if list_sizes(rewritten) != list_sizes(original):
        errors.append(f"пунктов в списках {list_sizes(rewritten) or 'нет'}, "
                      f"в оригинале {list_sizes(original) or 'нет'}")
    extra = sorted(tag_names(rewritten) - tag_names(original))
    if extra:
        errors.append(f"теги, которых нет в оригинале: {', '.join(extra)}")
    shared = shared_shingles(original, rewritten)
    if shared > MAX_SHARED_SHINGLES:
        errors.append(f"слишком близко к оригиналу: совпадает {round(shared * 100)} % "
                      f"цепочек из {SHINGLE_WORDS} слов, допустимо до "
                      f"{round(MAX_SHARED_SHINGLES * 100)} % — перестрой фразы")
    return errors
