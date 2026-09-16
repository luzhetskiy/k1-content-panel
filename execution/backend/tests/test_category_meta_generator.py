from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.ai.text import JsonResult
from app.category_meta.generator import MetaValidationError, generate_category
from app.category_meta.seeds import SeedsError
from app.models.category_meta import CategoryMeta, WordstatCall
from app.models.site import Site
from app.seed import seed_prompts
from app.wordstat.cache import cache_put
from app.wordstat.quota import QuotaExceeded

TOP = {"totalCount": "96275", "results": [
    {"phrase": "фанера", "count": "96275"}, {"phrase": "фанера купить", "count": "12238"},
    {"phrase": "фанера цена", "count": "5300"}, {"phrase": "фанера москва", "count": "2338"},
    {"phrase": "купить фанеру в москве", "count": "1552"},
    {"phrase": "лист фанеры цена", "count": "2758"},
    {"phrase": "фанера влагостойкая", "count": "6325"},
    {"phrase": "ламинированная фанера", "count": "8428"},
    {"phrase": "фанера фсф", "count": "3058"}, {"phrase": "фанера фк", "count": "2495"},
    {"phrase": "леруа фанера", "count": "1206"}]}
EXACT = {'"!купить !фанера"': {"totalCount": "678"}, '"!купить !фанеру"': {"totalCount": "234"}}

VALID = {
    "title": "Фанера в Москве — купить по выгодной цене | Стройбаза",
    "h1": "Фанера в Москве",
    "meta_description": ("Фанера в Москве по выгодной цене: влагостойкая ФСФ, ФК, ламинированная и "
                         "берёзовая. Листы 1525×1525 и 2440×1220 мм, толщина 4–40 мм. "
                         "Доставка по Москве."),
    "meta_keywords": ("фанера, фанера купить, фанера цена, фанера москва, купить фанеру в москве, "
                      "лист фанеры цена, фанера влагостойкая, ламинированная фанера, фанера фсф, "
                      "фанера фк"),
    "ai_keywords": ("где купить фанеру в Москве, какая фанера подходит для пола, "
                    "влагостойкая фанера ФСФ цена, фанера ФК или ФСФ что выбрать, "
                    "ламинированная фанера для опалубки, фанера 18 мм купить в Москве, "
                    "лист фанеры 1525×1525 цена, берёзовая фанера для мебели, "
                    "фанера с доставкой по Москве, сколько стоит лист фанеры"),
}


class FakeWordstat:
    def __init__(self):
        self.calls = []

    def top_requests(self, phrase, region_id, num_phrases=300):
        self.calls.append((phrase, region_id, num_phrases))
        return EXACT.get(phrase) or (TOP if phrase == "фанера" else {"totalCount": "3"})


class FakeText:
    model = "m"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        return JsonResult(self.answers.pop(0), 100, 50, 0.3)


class FakeSite:
    def __init__(self):
        self.created = []

    def list_metatags(self):
        return []

    def create_metatag(self, url, fields):
        self.created.append((url, fields))
        return {"id": 7}


@pytest.fixture
def site(db_session):
    seed_prompts(db_session)
    row = Site(name="Стройбаза", domain="s.ru", base_url="https://s.ru", api_token_enc="e",
               site_description="Интернет-магазин стройматериалов, доставка по Москве",
               city="Москва", city_in="в Москве", wordstat_region_id=213, brand="Стройбаза")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def fanera(db_session, site):
    row = CategoryMeta(site_id=site.id, remote_id=46, name="Фанера",
                       path="Листовые материалы / Фанера",
                       url="/catalog/category/listovye-materialy/fanera/", status="in_work",
                       seed_phrase="фанера", seed_source_name="Фанера", form_nominative="фанера",
                       form_buy="купить фанеру", form_price="цена фанеры")
    db_session.add(row)
    db_session.commit()
    return row


def run(db, category, site, *, wordstat=None, text=None, site_client=None, limit=100,
        usage=None):
    wordstat = wordstat or FakeWordstat()
    generate_category(db, category, site, wordstat_factory=lambda: wordstat,
                      text_client=text or FakeText([VALID]), site_client=site_client or FakeSite(),
                      limit=limit, stoplist=["леруа"],
                      record_usage=lambda *args: (usage.append(args) if usage is not None else None))
    return wordstat


def test_full_cycle_with_form_check(db_session, site, fanera):
    site_client, usage = FakeSite(), []
    wordstat = run(db_session, fanera, site, site_client=site_client, usage=usage)
    assert wordstat.calls == [("фанера", 213, 300), ('"!купить !фанера"', 213, 1),
                              ('"!купить !фанеру"', 213, 1)]
    assert (fanera.chosen_form, fanera.nominative_count, fanera.declined_count) == \
        ("nominative", 678, 234)
    assert fanera.total_count == 96275 and fanera.low_demand is False
    assert fanera.status == "done" and fanera.title == VALID["title"]
    assert site_client.created[0][0] == "/catalog/category/listovye-materialy/fanera/"
    assert usage == [(100, 50, 0.3)]
    assert db_session.scalar(select(func.count()).select_from(WordstatCall)) == 3


def test_same_forms_need_one_query(db_session, site, fanera):
    fanera.seed_phrase = fanera.form_nominative = "профнастил"
    fanera.form_buy = "купить профнастил"
    answer = {**VALID, "title": "Купить профнастил в Москве по выгодной цене | Стройбаза",
              "h1": "Профнастил в Москве", "meta_keywords": "профнастил москва",
              "meta_description": VALID["meta_description"].replace("Фанера", "Профнастил")}
    wordstat = run(db_session, fanera, site, text=FakeText([answer]))
    assert wordstat.calls == [("профнастил", 213, 300)]
    assert fanera.chosen_form == "declined" and fanera.nominative_count is None
    assert fanera.low_demand is True


def test_cache_hit_skips_wordstat_and_quota(db_session, site, fanera):
    cache_put(db_session, "top", "фанера", 213, TOP)
    for phrase, body in EXACT.items():
        cache_put(db_session, "exact", phrase, 213, body)

    def no_client():
        raise AssertionError("всё в кеше — клиент Wordstat не нужен")

    generate_category(db_session, fanera, site, wordstat_factory=no_client,
                      text_client=FakeText([VALID]), site_client=FakeSite(), limit=100,
                      stoplist=[], record_usage=lambda *a: None)
    assert fanera.status == "done"
    assert db_session.scalar(select(func.count()).select_from(WordstatCall)) == 0


def test_quota_exhausted_stops_before_llm(db_session, site, fanera):
    db_session.add_all([WordstatCall() for _ in range(99)])
    db_session.commit()
    wordstat, text = FakeWordstat(), FakeText([VALID])
    with pytest.raises(QuotaExceeded) as err:
        run(db_session, fanera, site, wordstat=wordstat, text=text)
    assert err.value.seconds > 0
    assert wordstat.calls == [] and text.prompts == []


def test_stoplist_phrases_do_not_reach_prompt(db_session, site, fanera):
    text = FakeText([VALID])
    run(db_session, fanera, site, text=text)
    assert "фанера купить — 12238" in text.prompts[0]
    assert "леруа" not in text.prompts[0]


def test_invalid_answer_retried_with_violations(db_session, site, fanera):
    text = FakeText([{**VALID, "h1": ""}, VALID])
    run(db_session, fanera, site, text=text)
    assert len(text.prompts) == 2
    assert "- h1 пустой" in text.prompts[1]
    assert fanera.status == "done"


def test_invalid_twice_raises_and_publishes_nothing(db_session, site, fanera):
    site_client = FakeSite()
    with pytest.raises(MetaValidationError) as err:
        run(db_session, fanera, site, text=FakeText([{**VALID, "h1": ""}, ["не объект"]]),
            site_client=site_client)
    assert "модель вернула не JSON-объект" in str(err.value)
    assert site_client.created == []
    # факты Wordstat сохранены до LLM — их видно в карточке
    assert fanera.chosen_form == "nominative" and fanera.total_count == 96275


def test_category_without_seed(db_session, site, fanera):
    fanera.seed_phrase = ""
    with pytest.raises(SeedsError):
        run(db_session, fanera, site)
