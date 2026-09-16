from types import SimpleNamespace

import pytest

from app.ai.text import JsonResult
from app.category_meta.seeds import (
    SeedsError, apply_seeds, generate_seeds, needs_seed, seed_lines,
)
from app.models.category_meta import CategoryMeta
from app.models.site import Site
from app.seed import seed_prompts


def category(remote_id, name, path="", **kwargs):
    return CategoryMeta(remote_id=remote_id, name=name, path=path, **kwargs)


def test_needs_seed_for_new_and_renamed():
    assert needs_seed(category(1, "Фанера"))
    assert not needs_seed(category(1, "Фанера", seed_phrase="фанера", seed_source_name="Фанера"))
    assert needs_seed(category(1, "Фанера ФК", seed_phrase="фанера", seed_source_name="Фанера"))


def test_seed_lines_use_path():
    assert seed_lines([category(46, "Фанера", "Листовые материалы / Фанера"),
                       category(7, "Крепеж")]) == ["46: Листовые материалы / Фанера", "7: Крепеж"]


def test_apply_seeds_fills_forms_and_reports_missing():
    fanera, ugolok, broken = category(46, "Фанера"), category(50, "Уголок металлический"), category(51, "Х")
    data = [{"id": "46", "phrase": "фанера", "nominative": "фанера", "buy": "купить  фанеру",
             "price": "цена фанеры"},
            {"id": 51, "phrase": "х", "nominative": "", "buy": "купить х", "price": "цена х"},
            "мусор", {"id": "не число"}]
    missing = apply_seeds([fanera, ugolok, broken], data)
    assert missing == [ugolok, broken]
    assert (fanera.seed_phrase, fanera.form_buy, fanera.seed_source_name) == \
        ("фанера", "купить фанеру", "Фанера")
    assert not broken.seed_phrase


def test_apply_seeds_rejects_non_list():
    with pytest.raises(SeedsError):
        apply_seeds([category(1, "А")], {"id": 1})


def test_generate_seeds_chunks_and_records_usage(db_session, monkeypatch):
    monkeypatch.setattr("app.category_meta.seeds.SEEDS_CHUNK", 2)
    seed_prompts(db_session)
    site = Site(name="С", domain="s.ru", base_url="https://s.ru", api_token_enc="e")
    db_session.add(site)
    db_session.commit()
    rows = [category(i, f"Категория {i}", site_id=site.id) for i in (1, 2, 3)]
    db_session.add_all(rows)
    db_session.commit()
    prompts, usage = [], []

    def complete_json(prompt):
        prompts.append(prompt)
        ids = [int(line.split(":")[0]) for line in prompt.splitlines()
               if line[:1].isdigit() and ": Категория" in line]
        return JsonResult([{"id": i, "phrase": f"ф{i}", "nominative": f"ф{i}",
                            "buy": f"купить ф{i}", "price": f"цена ф{i}"} for i in ids], 10, 5, 0.1)

    missing = generate_seeds(db_session, site, rows, SimpleNamespace(complete_json=complete_json),
                             lambda tp, tc, cost: usage.append((tp, tc, cost)))
    assert missing == []
    assert len(prompts) == 2 and len(usage) == 2
    assert [r.seed_phrase for r in rows] == ["ф1", "ф2", "ф3"]
