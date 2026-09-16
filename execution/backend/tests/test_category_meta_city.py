from types import SimpleNamespace

import pytest

from app.ai.text import TextResult
from app.category_meta.city import CityFormError, city_in_for


def client(answer, prompts=None):
    def complete_text(prompt):
        if prompts is not None:
            prompts.append(prompt)
        return TextResult(answer, 1, 1, 0.0)
    return SimpleNamespace(complete_text=complete_text)


def test_city_in_normalizes_answer():
    prompts = []
    assert city_in_for(client(" «Во Владимире» \n", prompts), " Владимир ") == "во Владимире"
    assert "«Владимир»" in prompts[0]


@pytest.mark.parametrize("answer,expected", [
    # 2026-09-16 на живой проверке модель вернула всю фразу из примера промпта
    ("купить стройматериалы в Москве", "в Москве"),
    ("Ответ: в Великом Новгороде.", "в Великом Новгороде"),
    ("в Ростове-на-Дону", "в Ростове-на-Дону"),
])
def test_city_in_extracted_from_longer_answer(answer, expected):
    assert city_in_for(client(answer), "Город") == expected


def test_city_in_rejects_answer_without_preposition():
    with pytest.raises(CityFormError):
        city_in_for(client("Москве"), "Москва")
    with pytest.raises(CityFormError):
        city_in_for(client("не знаю такого города"), "Москва")
