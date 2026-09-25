import re
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.ai.text import JsonResult, TextResult
from app.category_meta.generator import MetaValidationError, generate_category
from app.category_meta.seeds import SeedsError
from app.category_meta.seo_text import plain_text
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


def seo_answer(phrase, city_in="в Москве", size=2600):
    block = f"<h2>{phrase.capitalize()} {city_in}</h2><p>Как выбрать {phrase}: виды и назначение.</p>"
    html = block
    while len(plain_text(html)) < size:
        html += block
    return html


class FakeText:
    """Промпты тегов — в prompts, SEO-текста — в seo_prompts; ответ на SEO-текст
    по умолчанию собирается из фразы промпта, чтобы тесты тегов его не касались."""
    model = "m"

    def __init__(self, answers, seo=None):
        self.answers = list(answers)
        self.seo = list(seo or [])
        self.prompts, self.seo_prompts, self.seo_reasoning = [], [], []

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        return JsonResult(self.answers.pop(0), 100, 50, 0.3)

    def complete_text(self, prompt, *, reasoning=True):
        self.seo_prompts.append(prompt)
        self.seo_reasoning.append(reasoning)
        if self.seo:
            return TextResult(self.seo.pop(0), 300, 900, 0.5)
        phrase = re.search(r"Поисковая фраза: (.+?)\. Город", prompt).group(1)
        return TextResult(seo_answer(phrase), 300, 900, 0.5)


class FakeSite:
    def __init__(self, metatags=None):
        self.metatags = list(metatags or [])
        self.created, self.updated = [], []

    def list_metatags(self):
        return self.metatags

    def create_metatag(self, url, fields):
        self.created.append((url, fields))
        return {"id": 7}

    def update_metatag(self, metatag_id, fields):
        self.updated.append((metatag_id, fields))
        return {"id": metatag_id}


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
    assert site_client.created[0][1]["seo_text"].startswith("<h2>Фанера в Москве</h2>")
    assert fanera.seo_text == site_client.created[0][1]["seo_text"]
    assert usage == [(100, 50, 0.3), (300, 900, 0.5)]
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
    db_session.add_all([WordstatCall() for _ in range(100)])
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


GKL_TOPS = {
    "гипсокартон": {"totalCount": "87575", "results": [
        {"phrase": "гипсокартон", "count": "87575"}, {"phrase": "гипсокартон купить", "count": "3221"},
        {"phrase": "гипсокартон цена", "count": "2100"}, {"phrase": "влагостойкий гипсокартон", "count": "5000"},
        {"phrase": "гипсокартон москва", "count": "900"}]},
    "гкл": {"totalCount": "24437", "results": [
        {"phrase": "гкл", "count": "24437"}, {"phrase": "гкл купить", "count": "492"}]},
    "гипсокартонный лист": {"totalCount": "769", "results": [
        {"phrase": "гипсокартонный лист", "count": "769"}]},
}

GKL_VALID = {
    # формы совпадают («купить гипсокартон») → chosen_form=declined → title с «купить»
    "title": "Купить гипсокартон в Москве по выгодной цене | Стройбаза",
    "h1": "Гипсокартон в Москве",
    "meta_description": ("Гипсокартон в Москве по выгодной цене: обычный и влагостойкий ГКЛ Кнауф и "
                         "Волма, листы 12,5 мм для стен и потолков. Доставка по Москве, расчёт "
                         "в калькуляторе."),
    "meta_keywords": "гипсокартон купить, гипсокартон цена, влагостойкий гипсокартон, гкл купить, "
                     "гипсокартон москва",
    "ai_keywords": ", ".join(f"вопрос про гипсокартон номер {i}" for i in range(10)),
}


class GklWordstat(FakeWordstat):
    def top_requests(self, phrase, region_id, num_phrases=300):
        self.calls.append((phrase, region_id, num_phrases))
        return GKL_TOPS.get(phrase, {"totalCount": "0"})


@pytest.fixture
def gkl(db_session, site):
    variants = [{"phrase": p, "nominative": p, "buy": f"купить {p}", "price": f"цена {p}"}
                for p in ("гкл", "гипсокартон", "гипсокартонный лист")]
    row = CategoryMeta(site_id=site.id, remote_id=39, name="ГКЛ", path="Листовые материалы / ГКЛ",
                       url="/catalog/category/listovye-materialy/gipsokartonnyj-list-gkl/",
                       status="in_work", seed_phrase="гкл", seed_source_name="ГКЛ",
                       form_nominative="гкл", form_buy="купить гкл", form_price="цена гкл",
                       candidates_json=variants)
    db_session.add(row)
    db_session.commit()
    return row


def test_most_searched_variant_wins(db_session, site, gkl):
    wordstat, text = GklWordstat(), FakeText([{"same_product": ["гкл", "гипсокартон", "гипсокартонный лист"]}, GKL_VALID])
    run(db_session, gkl, site, wordstat=wordstat, text=text)
    assert wordstat.calls == [("гкл", 213, 300), ("гипсокартон", 213, 300),
                              ("гипсокартонный лист", 213, 300)]
    assert (gkl.seed_phrase, gkl.form_buy, gkl.total_count) == \
        ("гипсокартон", "купить гипсокартон", 87575)
    assert [(v["phrase"], v["count"], v["same_product"]) for v in gkl.candidates_json] == \
        [("гкл", 24437, True), ("гипсокартон", 87575, True), ("гипсокартонный лист", 769, True)]
    assert gkl.status == "done" and gkl.title == GKL_VALID["title"]
    # лидер — не первый вариант, поэтому сначала проверка смысла по фразам Wordstat
    assert "гипсокартон — 87575: гипсокартон, гипсокартон купить" in text.prompts[0]
    # фразы проигравших вариантов — в промпте тегов отдельным списком и допустимы в keywords
    assert "гкл — 24437: гкл, гкл купить" in text.prompts[1]
    assert "гипсокартон купить — 3221" in text.prompts[1]


def test_duplicate_variants_saved_before_dedup_are_queried_once(db_session, site, gkl):
    gkl.candidates_json = [*gkl.candidates_json,
                           {"phrase": "лист гипсокартонный", "nominative": "лист гипсокартонный",
                            "buy": "купить лист гипсокартонный", "price": "цена"}]
    wordstat = GklWordstat()
    run(db_session, gkl, site, wordstat=wordstat, text=FakeText([{"same_product": ["гкл", "гипсокартон", "гипсокартонный лист"]}, GKL_VALID]))
    assert [call[0] for call in wordstat.calls] == ["гкл", "гипсокартон", "гипсокартонный лист"]


PSB_TOPS = {
    "пенопласт": {"totalCount": "29949", "results": [
        {"phrase": "пенопласт", "count": "29949"}, {"phrase": "пенопласт купить", "count": "4100"},
        {"phrase": "пенопласт цена", "count": "2500"}, {"phrase": "пенопласт москва", "count": "700"}]},
    "пенополистирол": {"totalCount": "20244", "results": [
        {"phrase": "пенополистирол", "count": "20244"},
        {"phrase": "пенополистирол купить", "count": "1500"}]},
    "псб": {"totalCount": "274344", "results": [
        {"phrase": "псб", "count": "274344"}, {"phrase": "псб банк", "count": "90000"},
        {"phrase": "псб онлайн", "count": "40000"}]},
}

PSB_VALID = {
    "title": "Купить пенопласт в Москве по выгодной цене | Стройбаза",
    "h1": "Пенопласт в Москве",
    "meta_description": ("Пенопласт в Москве по выгодной цене: листы ПСБ-С 15, 25 и 35 для утепления "
                         "стен, пола и фасада, пенополистирол разной толщины. Доставка по Москве."),
    "meta_keywords": "пенопласт купить, пенопласт цена, пенопласт москва, пенополистирол купить",
    "ai_keywords": ", ".join(f"вопрос про пенопласт номер {i}" for i in range(10)),
}


class PsbWordstat(FakeWordstat):
    def top_requests(self, phrase, region_id, num_phrases=300):
        self.calls.append((phrase, region_id, num_phrases))
        return PSB_TOPS.get(phrase, {"totalCount": "0"})


@pytest.fixture
def penoplast(db_session, site):
    variants = [{"phrase": p, "nominative": p, "buy": f"купить {p}", "price": f"цена {p}"}
                for p in ("пенопласт", "пенополистирол", "псб")]
    row = CategoryMeta(site_id=site.id, remote_id=90, name="Пенопласт",
                       path="Теплоизоляция / Пенопласт", url="/catalog/category/teploizolyaciya/penoplast/",
                       status="in_work", seed_phrase="пенопласт", seed_source_name="Пенопласт",
                       form_nominative="пенопласт", form_buy="купить пенопласт",
                       form_price="цена пенопласт", candidates_json=variants)
    db_session.add(row)
    db_session.commit()
    return row


def test_variant_about_other_thing_is_rejected(db_session, site, penoplast):
    """«ПСБ» по запросам — банк: 274 тыс. запросов не должны отдать ей пенопласт
    (живая проверка 2026-09-16, решение владельца — строго тот же товар)."""
    text = FakeText([{"same_product": ["пенопласт", "пенополистирол"]}, PSB_VALID])
    run(db_session, penoplast, site, wordstat=PsbWordstat(), text=text)
    assert "псб — 274344: псб, псб банк, псб онлайн" in text.prompts[0]
    assert "Пенопласт" in text.prompts[0] and "Теплоизоляция / Пенопласт" in text.prompts[0]
    assert (penoplast.seed_phrase, penoplast.total_count) == ("пенопласт", 29949)
    assert [(v["phrase"], v["same_product"]) for v in penoplast.candidates_json] == \
        [("пенопласт", True), ("пенополистирол", True), ("псб", False)]
    # отвергнутый вариант не подсказывает keywords
    assert "пенополистирол — 20244" in text.prompts[1]
    assert "псб банк" not in text.prompts[1]
    assert penoplast.status == "done"


def test_catalog_variant_is_kept_when_llm_approves_nothing(db_session, site, penoplast):
    # пенополистирол отвергнут — его фразы из keywords выкидываются
    text = FakeText([{"что-то": "не то"}, PSB_VALID])
    run(db_session, penoplast, site, wordstat=PsbWordstat(), text=text)
    assert penoplast.seed_phrase == "пенопласт"
    assert [v["same_product"] for v in penoplast.candidates_json] == [True, False, False]
    assert penoplast.meta_keywords == "пенопласт купить, пенопласт цена, пенопласт москва"


def test_saved_verdicts_are_reused(db_session, site, penoplast):
    penoplast.candidates_json = [
        {**v, "same_product": v["phrase"] != "псб"} for v in penoplast.candidates_json]
    db_session.commit()
    text = FakeText([PSB_VALID])
    run(db_session, penoplast, site, wordstat=PsbWordstat(), text=text)
    assert len(text.prompts) == 1          # только теги, проверка смысла не повторяется
    assert penoplast.seed_phrase == "пенопласт"


def test_no_check_when_first_variant_leads(db_session, site, fanera):
    fanera.candidates_json = [{"phrase": "фанера", "nominative": "фанера", "buy": "купить фанеру",
                               "price": "цена фанеры"},
                              {"phrase": "фанерный лист", "nominative": "фанерный лист",
                               "buy": "купить фанерный лист", "price": "цена фанерного листа"}]
    db_session.commit()
    text = FakeText([VALID])
    run(db_session, fanera, site, text=text)
    assert len(text.prompts) == 1
    assert "same_product" not in fanera.candidates_json[0]


def test_variant_tie_keeps_llm_order(db_session, site, gkl):
    tops = {"гкл": {"totalCount": "10"}, "гипсокартон": {"totalCount": "10"},
            "гипсокартонный лист": {"totalCount": "10"}}

    class TieWordstat(FakeWordstat):
        def top_requests(self, phrase, region_id, num_phrases=300):
            self.calls.append((phrase, region_id, num_phrases))
            return tops.get(phrase, {"totalCount": "0"})

    with pytest.raises(MetaValidationError):
        run(db_session, gkl, site, wordstat=TieWordstat(), text=FakeText([{}, {}]))
    assert gkl.seed_phrase == "гкл"


def test_quota_for_form_check_after_variants_are_cached(db_session, site, fanera):
    fanera.candidates_json = [{"phrase": "фанера", "nominative": "фанера", "buy": "купить фанеру",
                               "price": "цена фанеры"},
                              {"phrase": "фанерный лист", "nominative": "фанерный лист",
                               "buy": "купить фанерный лист", "price": "цена фанерного листа"}]
    db_session.add_all([WordstatCall() for _ in range(98)])
    db_session.commit()
    wordstat = FakeWordstat()
    with pytest.raises(QuotaExceeded):
        run(db_session, fanera, site, wordstat=wordstat)
    # два варианта уместились в квоту и легли в кеш; на проверку формы квоты не хватило
    assert [call[0] for call in wordstat.calls] == ["фанера", "фанерный лист"]


def test_category_without_seed(db_session, site, fanera):
    fanera.seed_phrase = ""
    with pytest.raises(SeedsError):
        run(db_session, fanera, site)


def test_seo_text_gets_products_keywords_and_is_retried(db_session, site, fanera):
    fanera.product_names_json = ["Фанера ФК 1525х1525х10"]
    short = "```html\n<h2>Фанера в Москве</h2><p>Коротко.</p>\n```"
    text = FakeText([VALID], seo=[short, seo_answer("фанера")])
    site_client = FakeSite()
    run(db_session, fanera, site, text=text, site_client=site_client)
    assert len(text.seo_prompts) == 2
    assert "Фанера ФК 1525х1525х10" in text.seo_prompts[0]
    assert "фанера влагостойкая" in text.seo_prompts[0]          # keywords страницы
    assert "Заголовок страницы (h1): Фанера в Москве" in text.seo_prompts[0]
    assert "- SEO-текст должен быть 2500–3000 символов" in text.seo_prompts[1]
    assert text.seo_reasoning == [False, False]      # рассуждения модели выключены
    assert fanera.status == "done" and fanera.seo_text.startswith("<h2>")


def test_bad_seo_text_twice_publishes_nothing(db_session, site, fanera):
    site_client = FakeSite()
    with pytest.raises(MetaValidationError) as err:
        run(db_session, fanera, site, text=FakeText([VALID], seo=["", "<p>Фанера</p>"]),
            site_client=site_client)
    assert str(err.value).startswith("SEO-текст не прошёл проверку")
    assert site_client.created == [] and fanera.title == ""


def test_seo_only_keeps_tags_and_patches_only_seo_text(db_session, site, fanera):
    for name, value in VALID.items():
        setattr(fanera, name, value)
    db_session.commit()
    site_client = FakeSite([{"id": 39, "url": fanera.url, **VALID, "seo_text": ""}])
    fanera.previous_json = {}
    text = FakeText([])                     # промпт тегов не нужен
    generate_category(db_session, fanera, site, wordstat_factory=FakeWordstat,
                      text_client=text, site_client=site_client, limit=100, stoplist=[],
                      record_usage=lambda *a: None, seo_only=True)
    assert text.prompts == [] and len(text.seo_prompts) == 1
    assert site_client.updated == [(39, {"seo_text": fanera.seo_text})]
    assert fanera.title == VALID["title"] and fanera.status == "done"


def test_seo_only_needs_ready_tags(db_session, site, fanera):
    with pytest.raises(MetaValidationError, match="нет тегов"):
        generate_category(db_session, fanera, site, wordstat_factory=FakeWordstat,
                          text_client=FakeText([]), site_client=FakeSite(), limit=100,
                          stoplist=[], record_usage=lambda *a: None, seo_only=True)
