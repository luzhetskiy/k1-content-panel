"""Метатеги одной категории: Wordstat → выбор формы → LLM → проверка → сайт.

Порядок шагов важен для денег и квоты:
- клиент Wordstat создаётся лениво и ДО резервирования квоты — без ключа квота
  не тратится;
- статистика берётся из кеша, в Wordstat уходят только промахи;
- из вариантов названия («гкл», «гипсокартон») побеждает самый частотный;
- факты Wordstat сохраняются в категорию до вызова LLM — если теги не пройдут
  проверку, в карточке всё равно видно, что показал Wordstat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
ALTERNATIVE_PHRASES = 15 # сколько частых фраз показать у каждого проигравшего варианта
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
    # Проигравшие варианты названия: (фраза, всего запросов, её частые фразы).
    alternatives: list[tuple[str, int, list[tuple[str, int]]]] = field(default_factory=list)


def seed_variants(category: CategoryMeta) -> list[dict]:
    """Варианты названия от LLM; без них — единственный вариант из seed-полей."""
    variants = [{name: v.get(name, "") for name in ("phrase", "nominative", "buy", "price")}
                for v in (category.candidates_json or [])
                if isinstance(v, dict) and v.get("phrase")]
    return variants or [{"phrase": category.seed_phrase, "nominative": category.form_nominative,
                         "buy": category.form_buy, "price": category.form_price}]


def _load(db: Session, queries: list[tuple[str, str]], region_id: int | None, wordstat_factory,
          limit: int, now: datetime | None) -> dict[tuple[str, str], dict]:
    """Ответы Wordstat по запросам: из кеша, промахи — одним резервом квоты."""
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
    return bodies


def collect_facts(db: Session, category: CategoryMeta, region_id: int | None,
                  wordstat_factory, limit: int, now: datetime | None = None) -> WordstatFacts:
    """Сначала статистика по всем вариантам названия — побеждает самый частотный
    (при равенстве — первый, как предложила LLM); его формы записываются в
    категорию. Затем проверка склонения — уже для победителя. Два этапа — два
    резерва квоты: если на второй не хватит, варианты уже лежат в кеше."""
    variants = seed_variants(category)
    tops = _load(db, [(TOP_KIND, v["phrase"]) for v in variants], region_id,
                 wordstat_factory, limit, now)
    parsed = [(variant, parse_top(tops[(TOP_KIND, variant["phrase"])])) for variant in variants]
    winner, top = max(parsed, key=lambda item: item[1].total_count)
    category.seed_phrase, category.form_nominative = winner["phrase"], winner["nominative"]
    category.form_buy, category.form_price = winner["buy"], winner["price"]
    category.candidates_json = [{**variant, "count": result.total_count}
                                for variant, result in parsed]

    nominative_count = declined_count = None
    if forms_differ(category.form_nominative, category.form_buy):
        queries = [(EXACT_KIND, query)
                   for query in buy_queries(category.form_nominative, category.form_buy)]
        exact = _load(db, queries, region_id, wordstat_factory, limit, now)
        nominative_count = parse_top(exact[queries[0]]).total_count
        declined_count = parse_top(exact[queries[1]]).total_count
    return WordstatFacts(
        total_count=top.total_count, phrases=top.phrases,
        chosen_form=choose_form(nominative_count, declined_count),
        nominative_count=nominative_count, declined_count=declined_count,
        alternatives=[(variant["phrase"], result.total_count, result.phrases)
                      for variant, result in parsed if variant is not winner])


def generate_tags(db: Session, category: CategoryMeta, site, facts: WordstatFacts,
                  text_client, stoplist: list[str], record_usage) -> dict:
    phrases = filter_stoplist(facts.phrases, stoplist)
    alternatives = [(phrase, count, filter_stoplist(alt_phrases, stoplist))
                    for phrase, count, alt_phrases in facts.alternatives]
    ctx = MetaContext(form_nominative=category.form_nominative, form_buy=category.form_buy,
                      chosen_form=facts.chosen_form, city=site.city, city_in=site.city_in,
                      brand=site.brand,
                      wordstat_phrases=[phrase for phrase, _ in phrases]
                      + [phrase for _, _, alt in alternatives for phrase, _ in alt],
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
            "alternatives": [
                f"{phrase} — {count}: " + ", ".join(p for p, _ in alt[:ALTERNATIVE_PHRASES])
                for phrase, count, alt in alternatives],
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
