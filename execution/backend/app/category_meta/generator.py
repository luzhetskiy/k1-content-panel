"""Метатеги одной категории: Wordstat → выбор формы → LLM → проверка → сайт.

Порядок шагов важен для денег и квоты:
- клиент Wordstat создаётся лениво и ДО резервирования квоты — без ключа квота
  не тратится;
- статистика берётся из кеша, в Wordstat уходят только промахи;
- факты Wordstat сохраняются в категорию до вызова LLM — если теги не пройдут
  проверку, в карточке всё равно видно, что показал Wordstat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.ai.prompts import render_prompt, resolve_prompt
from app.category_meta.forms import (
    LOW_DEMAND_THRESHOLD, buy_queries, choose_form, filter_stoplist, forms_differ, sell_word,
)
from app.category_meta.publish import publish_metatag
from app.category_meta.seeds import SeedsError
from app.category_meta.validate import (
    TAG_FIELDS, MetaContext, normalize_tags, validate_tags,
)
from app.clock import utcnow
from app.models.category_meta import CategoryMeta
from app.wordstat.cache import cache_get, cache_put
from app.wordstat.client import parse_top
from app.wordstat.quota import QuotaExceeded, reserve

TOP_KIND = "top"
EXACT_KIND = "exact"
TOP_PHRASES = 300        # сколько фраз просить у topRequests
PROMPT_PHRASES = 100     # сколько из них показать модели
LLM_ATTEMPTS = 2         # первая попытка + одна с перечнем нарушений


class MetaValidationError(RuntimeError):
    pass


@dataclass
class WordstatFacts:
    total_count: int
    phrases: list[tuple[str, int]]
    chosen_form: str
    nominative_count: int | None
    declined_count: int | None


def wordstat_queries(category: CategoryMeta) -> list[tuple[str, str]]:
    queries = [(TOP_KIND, category.seed_phrase)]
    if forms_differ(category.form_nominative, category.form_buy):
        nominative, declined = buy_queries(category.form_nominative, category.form_buy)
        queries += [(EXACT_KIND, nominative), (EXACT_KIND, declined)]
    return queries


def collect_facts(db: Session, category: CategoryMeta, region_id: int | None,
                  wordstat_factory, limit: int, now: datetime | None = None) -> WordstatFacts:
    queries = wordstat_queries(category)
    bodies = {query: cache_get(db, query[0], query[1], region_id, now) for query in queries}
    missing = [query for query, body in bodies.items() if body is None]
    if missing:
        client = wordstat_factory()
        wait = reserve(db, len(missing), limit, now)
        if wait:
            raise QuotaExceeded(wait)
        for kind, phrase in missing:
            # У точной формы нужен только totalCount — список фраз не просим.
            body = client.top_requests(phrase, region_id, TOP_PHRASES if kind == TOP_KIND else 1)
            cache_put(db, kind, phrase, region_id, body, now)
            bodies[(kind, phrase)] = body
    top = parse_top(bodies[queries[0]])
    if len(queries) == 3:
        nominative_count = parse_top(bodies[queries[1]]).total_count
        declined_count = parse_top(bodies[queries[2]]).total_count
        chosen = choose_form(nominative_count, declined_count)
    else:
        nominative_count = declined_count = None
        chosen = choose_form(None, None)
    return WordstatFacts(top.total_count, top.phrases, chosen, nominative_count, declined_count)


def generate_tags(db: Session, category: CategoryMeta, site, facts: WordstatFacts,
                  text_client, stoplist: list[str], record_usage) -> dict:
    phrases = filter_stoplist(facts.phrases, stoplist)
    ctx = MetaContext(form_nominative=category.form_nominative, form_buy=category.form_buy,
                      chosen_form=facts.chosen_form, city=site.city, city_in=site.city_in,
                      brand=site.brand, wordstat_phrases=[phrase for phrase, _ in phrases],
                      stoplist=stoplist, site_description=site.site_description)
    template = resolve_prompt(db, "category_meta", site.id)
    violations: list[str] = []
    for _attempt in range(LLM_ATTEMPTS):
        prompt = render_prompt(template, {
            "site_name": site.name, "site_description": site.site_description,
            "category_name": category.name, "category_path": category.path or category.name,
            "form_nominative": category.form_nominative, "form_buy": category.form_buy,
            "form_price": category.form_price, "chosen_form": facts.chosen_form,
            "sell_word": sell_word(phrases), "city_in": site.city_in, "brand": site.brand,
            "total_count": facts.total_count,
            "phrases": [f"{phrase} — {count}" for phrase, count in phrases[:PROMPT_PHRASES]],
            "violations": violations,
        })
        result = text_client.complete_json(prompt)
        record_usage(result.tokens_prompt, result.tokens_completion, result.cost)
        try:
            tags = normalize_tags(result.data)
        except ValueError as exc:
            violations = [str(exc)]
            continue
        violations = validate_tags(tags, ctx)
        if not violations:
            return tags
    raise MetaValidationError("теги не прошли проверку: " + "; ".join(violations))


def generate_category(db: Session, category: CategoryMeta, site, *, wordstat_factory,
                      text_client, site_client, limit: int, stoplist: list[str],
                      record_usage, now: datetime | None = None) -> None:
    if not category.seed_phrase:
        raise SeedsError("у категории нет поисковой фразы — обновите метатеги проекта целиком")
    facts = collect_facts(db, category, site.wordstat_region_id, wordstat_factory, limit, now)
    category.chosen_form = facts.chosen_form
    category.nominative_count = facts.nominative_count
    category.declined_count = facts.declined_count
    category.total_count = facts.total_count
    category.low_demand = facts.total_count < LOW_DEMAND_THRESHOLD
    db.commit()

    tags = generate_tags(db, category, site, facts, text_client, stoplist, record_usage)
    publish_metatag(site_client, category, tags)
    for name in TAG_FIELDS:
        setattr(category, name, tags[name])
    category.status = "done"
    category.error_text = ""
    category.wait_until = None
    category.updated_at = utcnow()
    db.commit()
