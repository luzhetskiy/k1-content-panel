"""Поисковая фраза и формы для категорий — один вызов LLM на пачку категорий.

Фраза перезапрашивается только у новых и переименованных категорий
(seed_source_name != name): платить за неизменившееся дерево на каждом
запуске незачем."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.prompts import render_prompt, resolve_prompt
from app.models.category_meta import CategoryMeta

# Сколько категорий в одном запросе: 77 категорий stroybaza-moscow.ru — два
# вызова; ответ на 60 объектов укладывается в лимит вывода модели с запасом.
SEEDS_CHUNK = 60
SEED_FIELDS = ("phrase", "nominative", "buy", "price")
# Каждый вариант — запрос к Wordstat из квоты 100/час, поэтому не больше четырёх.
MAX_VARIANTS = 4


class SeedsError(RuntimeError):
    pass


def needs_seed(row: CategoryMeta) -> bool:
    return not row.seed_phrase or row.seed_source_name != row.name


def seed_lines(rows: list[CategoryMeta]) -> list[str]:
    return [f"{row.remote_id}: {row.path or row.name}" for row in rows]


def variant_key(phrase: str) -> frozenset[str]:
    """Wordstat в широком соответствии не различает порядок слов и дефис:
    «анкер клиновой» = «клиновой анкер», «блок-хаус» = «блок хаус». Одинаковый
    ключ — один и тот же запрос, второй раз квоту на него не тратим."""
    return frozenset(phrase.casefold().replace("-", " ").split())


def dedupe_variants(variants: list[dict]) -> list[dict]:
    result, seen = [], set()
    for variant in variants:
        key = variant_key(variant["phrase"])
        if key not in seen:
            seen.add(key)
            result.append(variant)
    return result


def _variants(item: dict) -> list[dict]:
    """Полные, без дублей, не больше MAX_VARIANTS. Элемент без "variants" —
    старый формат ответа (одна фраза прямо в объекте): отредактированный в
    админке промпт мог в нём остаться."""
    raw = item.get("variants") if isinstance(item.get("variants"), list) else [item]
    variants = []
    for candidate in raw:
        if not isinstance(candidate, dict):
            continue
        values = [" ".join(str(candidate.get(name) or "").split()) for name in SEED_FIELDS]
        if all(values):
            variants.append(dict(zip(SEED_FIELDS, values)))
    return dedupe_variants(variants)[:MAX_VARIANTS]


def apply_seeds(rows: list[CategoryMeta], data: object) -> list[CategoryMeta]:
    """Раскладывает ответ модели по категориям. Возвращает те, для которых
    не пришло ни одного полного варианта названия."""
    if not isinstance(data, list):
        raise SeedsError("модель вернула не JSON-массив поисковых фраз")
    by_id: dict[int, dict] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            by_id[int(item.get("id"))] = item
        except (TypeError, ValueError):
            continue
    missing = []
    for row in rows:
        variants = _variants(by_id.get(row.remote_id) or {})
        if not variants:
            missing.append(row)
            continue
        # Первый вариант — до Wordstat; победителя выберет app/category_meta/generator.py.
        first = variants[0]
        row.seed_phrase, row.form_nominative = first["phrase"], first["nominative"]
        row.form_buy, row.form_price = first["buy"], first["price"]
        row.candidates_json = variants
        row.seed_source_name = row.name
    return missing


def generate_seeds(db: Session, site, rows: list[CategoryMeta], text_client,
                   record_usage) -> list[CategoryMeta]:
    template = resolve_prompt(db, "category_seeds", site.id)
    missing: list[CategoryMeta] = []
    for start in range(0, len(rows), SEEDS_CHUNK):
        chunk = rows[start:start + SEEDS_CHUNK]
        prompt = render_prompt(template, {"site_name": site.name,
                                          "site_description": site.site_description,
                                          "categories": seed_lines(chunk)})
        result = text_client.complete_json(prompt)
        record_usage(result.tokens_prompt, result.tokens_completion, result.cost)
        missing += apply_seeds(chunk, result.data)
    db.commit()
    return missing
