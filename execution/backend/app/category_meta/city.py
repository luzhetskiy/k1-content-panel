"""Город с предлогом для тегов: «в Москве», «во Владимире».

Промпт не выносится в редактируемые: это грамматика, а не редакционная
политика, и ошибка здесь видна сразу — менеджер правит поле руками."""

from __future__ import annotations

import re

CITY_IN_PROMPT = """Поставь название города «{city}» в предложный падеж вместе с
правильным предлогом «в» или «во» — так, как оно встанет во фразу «купить
стройматериалы …». Примеры: Москва → в Москве; Владимир → во Владимире;
Тверь → в Твери; Великий Новгород → в Великом Новгороде.
Верни только эту фразу, без кавычек и пояснений."""

_CITY_IN = re.compile(r"^во?\s+\S", re.IGNORECASE)


class CityFormError(RuntimeError):
    pass


def city_in_for(text_client, city: str) -> str:
    result = text_client.complete_text(CITY_IN_PROMPT.format(city=city.strip()))
    value = " ".join(result.text.strip().strip("\"'«»").split())
    if not _CITY_IN.match(value) or len(value) > 100:
        raise CityFormError(f"модель вернула неожиданную форму города: {value[:100]!r}")
    return value[0].lower() + value[1:]
