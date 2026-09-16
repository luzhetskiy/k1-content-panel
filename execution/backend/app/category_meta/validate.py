"""Проверка тегов, которые вернула LLM. Правила — таблица «Правила полей» в
directions/2026-09-16-category-meta-design.md. Каждое нарушение — строка по-русски:
она уходит обратно в модель при повторе и в error_text категории."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TITLE_MAX = 70
H1_MAX = 60
DESCRIPTION_MIN = 140
DESCRIPTION_MAX = 170
KEYWORDS_MAX = 10
AI_KEYWORDS_MIN = 10
AI_KEYWORDS_MAX = 15

TAG_FIELDS = ("title", "h1", "meta_description", "meta_keywords", "ai_keywords")

# Утверждения о магазине, которые нельзя писать, если их нет в описании сайта.
# «опт» — словами целиком: подстрока «опт» есть в «оптимальный».
FORBIDDEN_CLAIMS = {
    "в наличии": r"в\s+наличии",
    "скидки": r"скидк",
    "оптом": r"\bопт(ом|ов\w*)\b",
}


@dataclass
class MetaContext:
    form_nominative: str
    form_buy: str
    chosen_form: str               # nominative | declined
    city: str                      # «Москва»
    city_in: str                   # «в Москве»
    brand: str
    wordstat_phrases: list[str] = field(default_factory=list)
    stoplist: list[str] = field(default_factory=list)
    site_description: str = ""


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def split_phrases(text: str) -> list[str]:
    return [" ".join(part.split()) for part in (text or "").split(",") if part.strip()]


def normalize_tags(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("модель вернула не JSON-объект с тегами")
    tags = {}
    for name in TAG_FIELDS:
        value = raw.get(name, "")
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        tags[name] = " ".join(str(value or "").split())
    # Видимые поля — с заглавной: модель иногда начинает title со строчной
    # («купить профнастил в Москве…», живая проверка 2026-09-16).
    for name in ("title", "h1", "meta_description"):
        tags[name] = tags[name][:1].upper() + tags[name][1:]
    tags["meta_keywords"] = ", ".join(p.casefold() for p in split_phrases(tags["meta_keywords"]))
    tags["ai_keywords"] = ", ".join(split_phrases(tags["ai_keywords"]))
    return tags


def allowed_keywords(ctx: MetaContext) -> set[str]:
    nominative, city_in = _norm(ctx.form_nominative), _norm(ctx.city_in)
    allowed = {_norm(phrase) for phrase in ctx.wordstat_phrases}
    allowed |= {f"{nominative} {_norm(ctx.city)}", f"{nominative} {city_in}",
                f"{_norm(ctx.form_buy)} {city_in}"}
    return allowed


def validate_tags(tags: dict, ctx: MetaContext) -> list[str]:
    errors: list[str] = []
    title, h1, description = tags["title"], tags["h1"], tags["meta_description"]
    city_in = _norm(ctx.city_in)
    suffix = f" | {ctx.brand}"

    if len(title) > TITLE_MAX:
        errors.append(f"title длиннее {TITLE_MAX} символов ({len(title)})")
    if not title.endswith(suffix):
        errors.append(f"title должен оканчиваться на «{suffix}»")
    lead = ctx.form_nominative if ctx.chosen_form == "nominative" else ctx.form_buy
    if not _norm(title).startswith(_norm(lead)):
        errors.append(f"title должен начинаться с «{lead}»")
    if city_in not in _norm(title):
        errors.append(f"в title нет «{ctx.city_in}»")
    if "купит" not in _norm(title) and "цен" not in _norm(title):
        errors.append("в title нет продающего слова «купить» или «цена»")

    if not h1:
        errors.append("h1 пустой")
    if len(h1) > H1_MAX:
        errors.append(f"h1 длиннее {H1_MAX} символов ({len(h1)})")
    if _norm(ctx.brand) in _norm(h1):
        errors.append("в h1 не должно быть бренда")
    if city_in not in _norm(h1):
        errors.append(f"в h1 нет «{ctx.city_in}»")
    if _norm(h1) == _norm(title.removesuffix(suffix)):
        errors.append("h1 не должен повторять title")

    if not DESCRIPTION_MIN <= len(description) <= DESCRIPTION_MAX:
        errors.append(f"description должен быть {DESCRIPTION_MIN}–{DESCRIPTION_MAX} "
                      f"символов ({len(description)})")
    if city_in not in _norm(description):
        errors.append(f"в description нет «{ctx.city_in}»")
    site_description = _norm(ctx.site_description)
    for label, pattern in FORBIDDEN_CLAIMS.items():
        if re.search(pattern, _norm(description)) and not re.search(pattern, site_description):
            errors.append(f"в description утверждение «{label}», которого нет в описании сайта")

    keywords = split_phrases(tags["meta_keywords"])
    if not keywords:
        errors.append("keywords пустые")
    if len(keywords) > KEYWORDS_MAX:
        errors.append(f"keywords: больше {KEYWORDS_MAX} фраз ({len(keywords)})")
    allowed = allowed_keywords(ctx)
    unknown = [phrase for phrase in keywords if _norm(phrase) not in allowed]
    if unknown:
        errors.append("keywords не из статистики Wordstat: " + ", ".join(unknown))

    ai_keywords = split_phrases(tags["ai_keywords"])
    if not AI_KEYWORDS_MIN <= len(ai_keywords) <= AI_KEYWORDS_MAX:
        errors.append(f"ai_keywords: нужно {AI_KEYWORDS_MIN}–{AI_KEYWORDS_MAX} фраз "
                      f"({len(ai_keywords)})")

    for name in TAG_FIELDS:
        hits = [stop for stop in ctx.stoplist if stop in _norm(tags[name])]
        if hits:
            errors.append(f"в {name} слова из стоп-листа: {', '.join(hits)}")
    return errors
