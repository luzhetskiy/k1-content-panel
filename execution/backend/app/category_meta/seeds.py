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


class SeedsError(RuntimeError):
    pass


def needs_seed(row: CategoryMeta) -> bool:
    return not row.seed_phrase or row.seed_source_name != row.name


def seed_lines(rows: list[CategoryMeta]) -> list[str]:
    return [f"{row.remote_id}: {row.path or row.name}" for row in rows]


def apply_seeds(rows: list[CategoryMeta], data: object) -> list[CategoryMeta]:
    """Раскладывает ответ модели по категориям. Возвращает те, для которых
    фраза не пришла или пришла неполной."""
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
        item = by_id.get(row.remote_id) or {}
        values = [" ".join(str(item.get(name) or "").split()) for name in SEED_FIELDS]
        if not all(values):
            missing.append(row)
            continue
        row.seed_phrase, row.form_nominative, row.form_buy, row.form_price = values
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
