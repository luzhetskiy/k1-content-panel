"""Выбор словоформы названия категории по статистике Wordstat.

Решение принимает код, а не LLM: модель получает готовую форму явной
инструкцией (directions/2026-09-16-category-meta-design.md, «Правила генерации»).
"""

from __future__ import annotations

BUY_PREFIX = "купить "

# Ниже этой частоты у обеих форм статистика ничего не говорит — берём
# грамотную склонённую форму.
MIN_FORM_COUNT = 10

# Меньше стольких запросов в месяц по фразе — категория помечается «мало данных».
LOW_DEMAND_THRESHOLD = 50


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def declined_object(form_buy: str) -> str:
    """«купить фанеру» → «фанеру». Без префикса — фраза как есть."""
    norm = " ".join((form_buy or "").split())
    if norm.casefold().startswith(BUY_PREFIX):
        return norm[len(BUY_PREFIX):]
    return norm


def forms_differ(form_nominative: str, form_buy: str) -> bool:
    """«купить профнастил» не склоняется — проверять в Wordstat нечего."""
    return _norm(form_nominative) != _norm(declined_object(form_buy))


def exact_query(phrase: str) -> str:
    """Точная форма для Wordstat: кавычки фиксируют число слов, «!» — словоформу
    каждого слова. «купить фанеру» → "!купить !фанеру"."""
    return '"' + " ".join(f"!{word}" for word in _norm(phrase).split()) + '"'


def buy_queries(form_nominative: str, form_buy: str) -> tuple[str, str]:
    """(несклонённая, склонённая) — «купить фанера» и «купить фанеру»."""
    return (exact_query(f"{BUY_PREFIX}{form_nominative}"),
            exact_query(f"{BUY_PREFIX}{declined_object(form_buy)}"))


def choose_form(nominative_count: int | None, declined_count: int | None) -> str:
    if nominative_count is None or declined_count is None:
        return "declined"
    if max(nominative_count, declined_count) < MIN_FORM_COUNT:
        return "declined"
    if nominative_count == declined_count:
        return "declined"
    return "nominative" if nominative_count > declined_count else "declined"


def sell_word(phrases: list[tuple[str, int]]) -> str:
    """Какое продающее слово чаще в запросах: «купить» или «цена»."""
    buy = sum(count for phrase, count in phrases if "купит" in phrase.casefold())
    price = sum(count for phrase, count in phrases if "цен" in phrase.casefold())
    return "цена" if price > buy else "купить"


def filter_stoplist(phrases: list[tuple[str, int]],
                    stoplist: list[str]) -> list[tuple[str, int]]:
    return [(phrase, count) for phrase, count in phrases
            if not any(stop in phrase.casefold() for stop in stoplist)]
