"""Город с предлогом для тегов: «в Москве», «во Владимире».

Промпт не выносится в редактируемые: это грамматика, а не редакционная
политика, и ошибка здесь видна сразу — менеджер правит поле руками."""

from __future__ import annotations

import re

CITY_IN_PROMPT = """Поставь название города «{city}» в предложный падеж с правильным
предлогом «в» или «во». Примеры: Москва → в Москве; Владимир → во Владимире;
Тверь → в Твери; Великий Новгород → в Великом Новгороде.
Верни ТОЛЬКО предлог и название города — например «в Москве», без других слов,
кавычек и пояснений."""

# Предлог и название из заглавных слов в КОНЦЕ ответа: 2026-09-16 модель вернула
# «купить стройматериалы в Москве» — лишние слова перед городом отбрасываем.
_CITY_IN = re.compile(r"(?:^|\s)(во?\s+[А-ЯЁA-Z][\w-]*(?:\s+[А-ЯЁA-Z][\w-]*)*)$", re.IGNORECASE)
_CAPITAL = re.compile(r"^[А-ЯЁA-Z]")


class CityFormError(RuntimeError):
    pass


def city_in_for(text_client, city: str) -> str:
    result = text_client.complete_text(CITY_IN_PROMPT.format(city=city.strip()))
    value = " ".join(result.text.strip().strip("\"'«».").split()).rstrip(".!")
    match = _CITY_IN.search(value)
    if match is None:
        raise CityFormError(f"модель вернула неожиданную форму города: {value[:100]!r}")
    phrase = match.group(1)
    preposition, _, name = phrase.partition(" ")
    # IGNORECASE нужен для «Во», но название города обязано быть с заглавной —
    # иначе «не знаю такого города» сошло бы за «в» + слово.
    if not _CAPITAL.match(name):
        raise CityFormError(f"модель вернула неожиданную форму города: {value[:100]!r}")
    return f"{preposition.lower()} {name}"
