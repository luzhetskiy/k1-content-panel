"""Справочник регионов Wordstat: поиск id региона по названию города."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.wordstat.cache import cache_get, cache_put
from app.wordstat.quota import QuotaExceeded, reserve

REGIONS_KIND = "regions"


@dataclass
class Region:
    id: int
    label: str
    path: str      # «Россия / Центр / Москва и Московская область / Москва»


def flatten_regions(tree: dict) -> list[Region]:
    result: list[Region] = []

    def walk(nodes, parents: list[str]) -> None:
        for node in nodes or []:
            label = str(node.get("label", ""))
            try:
                region_id = int(node.get("id"))
            except (TypeError, ValueError):
                region_id = None
            if region_id is not None:
                result.append(Region(region_id, label, " / ".join([*parents, label])))
            walk(node.get("children"), [*parents, label])

    walk(tree.get("regions"), [])
    return result


def search_regions(regions: list[Region], query: str, limit: int = 10) -> list[Region]:
    """Сначала точные совпадения названия, затем начинающиеся с запроса."""
    needle = query.strip().casefold()
    if not needle:
        return []
    exact = [r for r in regions if r.label.casefold() == needle]
    prefix = [r for r in regions if r.label.casefold().startswith(needle)
              and r.label.casefold() != needle]
    return (exact + prefix)[:limit]


def load_regions(db: Session, limit: int, client_factory) -> list[Region]:
    """Дерево регионов из кеша; при промахе — один запрос к Wordstat из квоты.
    client_factory() зовётся ДО резервирования: без ключа квота не тратится."""
    body = cache_get(db, REGIONS_KIND, "", 0)
    if body is None:
        client = client_factory()
        wait = reserve(db, 1, limit)
        if wait:
            raise QuotaExceeded(wait)
        body = client.regions_tree()
        cache_put(db, REGIONS_KIND, "", 0, body)
    return flatten_regions(body)
