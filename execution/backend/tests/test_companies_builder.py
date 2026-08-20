from unittest.mock import Mock, patch

import pytest

from app.ai.text import JsonResult, LLMError
from app.companies.builder import CompanyBuilder, logo_filename, slug_for_company
from app.companies.scrape import ScrapeError
from app.models.company import Company, CompanyBatch, CompanyInfo
from app.models.site import Site
from app.sites.client import SiteAPIError


@pytest.fixture
def site():
    return Site(id=1, name="С", domain="s.ru", base_url="https://s.ru",
               api_token_enc="e",
               builder_template_html="<div id=\"builder\"><h1 id=\"builder-main-title\">"
                                     "</h1></div>",
               builder_parent_id=10, tone_of_voice="деловой")


@pytest.fixture
def batch():
    return CompanyBatch(id=1, site_id=1, category_normalized="Дома под ключ",
                        teaser_category_id=3, teaser_city_id=1, teaser_location_id=1,
                        requested_count=1)


@pytest.fixture
def company(batch):
    c = Company(id=7, site_id=1, batch_id=batch.id, site_key="dom.ru",
               website="https://dom.ru", name="ООО Дом", region="Самара")
    c.batch = batch
    return c


def _seed_prompts(db):
    # Задача Task 13: у резолвера промпта (app/ai/prompts.py resolve_prompt)
    # нет дефолта в БД, пока его туда не положить — реальный код это делает
    # через app.seed.seed_prompts (см. app/api/admin_prompts.py и тесты
    # test_articles_builder.py::prepared). Без этого resolve_prompt всегда
    # бросает PromptError("промпт 'builder_text' не найден") ещё до того, как
    # управление дойдёт до замоканного text_client, и тесты на LLM-путь
    # проверяли бы не то, что заявлено.
    from app.seed import seed_prompts

    seed_prompts(db)


def _builder(db, company, site, text_client=None, site_client=None, scrape=None, logo_fn=None):
    return CompanyBuilder(
        db=db, company=company, site=site,
        text_client=text_client or Mock(complete_json=Mock(return_value=JsonResult(
            data={"about_company": "Строим дома.", "specialization": "Каркасные дома.",
                 "projects_services": "50 проектов.", "benefits": "Гарантия 5 лет."},
            tokens_prompt=10, tokens_completion=20, cost=0.01))),
        site_client=site_client or Mock(
            create_page=Mock(return_value={"id": 99, "url": "/s/ooo-dom-samara/"}),
            create_teaser=Mock(return_value=555),
            upload_file=Mock(return_value="/media/uploads/service-img/cp-company-7-logo.webp"),
        ),
        scrape_fn=scrape or Mock(return_value="TITLE: ООО Дом\n\nСтроим дома под ключ."),
        logo_fn=logo_fn or Mock(return_value=""),
        job_run_id=None,
    )


def test_logo_filename_uses_company_prefix():
    assert logo_filename(7) == "cp-company-7-logo.webp"


def test_slug_for_company_transliterates_name_and_city():
    assert slug_for_company("ООО Дом", "Самара") == "ooo-dom-samara"


def test_build_success_publishes_company(db_session, site, company):
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               city_name="Самара", city_prepositional="Самаре",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    builder = _builder(db_session, company, site)
    builder.build()

    assert company.status == "published"
    assert company.remote_page_id == 99
    assert company.teaser_id == 555
    assert company.info.about_company == "Строим дома."
    assert "Строим дома под ключ" in company.info.scraped_text


def test_build_normalizes_phone_before_creating_teaser(db_session, site, company):
    """Сырой телефон из выгрузки Яндекс.Карт (в произвольном написании, с
    несколькими номерами через запятую и т.п.) должен уйти в create_teaser
    уже нормализованным — иначе API тизера отвечает 400 "Правильный формат
    телефона..." и вся сборка компании падает на этом шаге."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(
        company_id=company.id, builder_name="ООО Дом",
        city_name="Самара", city_prepositional="Самаре",
        contacts=[{"address": "ул. Ленина 1",
                  "phone_tel": "+7 (846) 277-06-05, 8 (846) 111-22-33"}]))
    db_session.commit()

    site_client = Mock(
        create_page=Mock(return_value={"id": 99, "url": "/s/ooo-dom-samara/"}),
        create_teaser=Mock(return_value=555),
        upload_file=Mock(return_value="/media/uploads/service-img/cp-company-7-logo.webp"),
    )
    builder = _builder(db_session, company, site, site_client=site_client)
    builder.build()

    assert company.status == "published"
    assert site_client.create_teaser.call_args.kwargs["phone"] == "78462770605"


def test_build_passes_coordinates_to_create_teaser(db_session, site, company):
    """CompanyInfo.coordinates ("lat, lon" из выгрузки Яндекс.Карт, см.
    app/api/company_batches.py::_company_info_from_candidate) должны уйти в
    create_teaser — иначе координаты на карточке-тизере целевого сайта не
    заполняются, хотя в выгрузке они есть (баг, найденный на проде для
    партии на stroybaza-moscow.ru)."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(
        company_id=company.id, builder_name="ООО Дом",
        city_name="Самара", city_prepositional="Самаре",
        coordinates="53.195873, 50.100202",
        contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    site_client = Mock(
        create_page=Mock(return_value={"id": 99, "url": "/s/ooo-dom-samara/"}),
        create_teaser=Mock(return_value=555),
        upload_file=Mock(return_value="/media/uploads/service-img/cp-company-7-logo.webp"),
    )
    builder = _builder(db_session, company, site, site_client=site_client)
    builder.build()

    assert company.status == "published"
    assert site_client.create_teaser.call_args.kwargs["coordinates"] == "53.195873, 50.100202"


def test_synced_template_builds_successfully(db_session, company):
    """Композиционный тест на стык двух половин фичи: шаблон, прошедший
    валидацию sync_builder_reference (проверяет id-контракт), должен быть
    достаточным и для CompanyBuilder.build() (fill_builder_template) — иначе
    требования к разметке в этих двух местах могут незаметно разъехаться."""
    from app.companies.reference import sync_builder_reference

    class FakeClient:
        def get_page(self, page_id):
            return {"id": page_id, "text": (
                '<div id="builder">'
                '<h1 id="builder-main-title"></h1>'
                '<div id="builder-contacts">'
                '<div id="builder-contacts-grid">'
                '<div id="builder-contact-1"></div>'
                '</div></div></div>'
            )}

    _seed_prompts(db_session)
    site = Site(id=1, name="С", domain="s.ru", base_url="https://s.ru",
               api_token_enc="e", builder_reference_id=77, builder_parent_id=10,
               tone_of_voice="деловой")
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               city_name="Самара", city_prepositional="Самаре",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    sync_builder_reference(db_session, site, FakeClient())

    builder = _builder(db_session, company, site)
    builder.build()

    assert company.status == "published"


def test_build_fails_company_on_scrape_error(db_session, site, company):
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом"))
    db_session.commit()

    builder = _builder(db_session, company, site,
                       scrape=Mock(side_effect=ScrapeError("нет ответа")))
    builder.build()

    assert company.status == "failed"
    assert "нет ответа" in company.error_text


def test_build_fails_company_on_llm_error(db_session, site, company):
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом"))
    db_session.commit()

    text_client = Mock(complete_json=Mock(side_effect=LLMError("модель недоступна")))
    builder = _builder(db_session, company, site, text_client=text_client)
    builder.build()

    assert company.status == "failed"
    assert "модель недоступна" in company.error_text


def test_build_requires_builder_template(db_session, company):
    _seed_prompts(db_session)
    site = Site(id=1, name="С", domain="s.ru", base_url="https://s.ru",
               api_token_enc="e", builder_template_html="", builder_parent_id=10)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом"))
    db_session.commit()

    builder = _builder(db_session, company, site)
    builder.build()

    assert company.status == "failed"
    assert "шаблон" in company.error_text.lower()


def test_build_fails_when_company_has_no_batch(db_session, site):
    """Компании, мигрированные из старого CLI (Task 15, ещё не реализован),
    получают batch_id=NULL — см. комментарий у Company.batch_id в
    app/models/company.py. build() обязан завершиться штатным
    status="failed", а не необработанным AttributeError на
    self.company.batch.teaser_category_id."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.commit()
    c = Company(id=7, site_id=1, batch_id=None, site_key="dom.ru",
               website="https://dom.ru", name="ООО Дом", region="Самара")
    c.batch = None
    db_session.add(c)
    db_session.add(CompanyInfo(company_id=c.id, builder_name="ООО Дом"))
    db_session.commit()

    builder = _builder(db_session, c, site)
    builder.build()

    assert c.status == "failed"
    assert "партии" in c.error_text.lower()


def test_build_fails_company_on_site_api_error(db_session, site, company):
    """Ошибка сайта на создании страницы/тизера обязана попасть в
    company.error_text через except-кортеж build() — до этого теста ни один
    сценарий не проверял SiteAPIError, пришедший именно от site_client
    (test_build_requires_builder_template проверяет только SiteAPIError,
    поднятую самим билдером)."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               city_name="Самара"))
    db_session.commit()

    site_client = Mock(
        create_page=Mock(side_effect=SiteAPIError("создание страницы: HTTP 403: Forbidden")),
        create_teaser=Mock(return_value=555),
        upload_file=Mock(),
    )
    builder = _builder(db_session, company, site, site_client=site_client)
    builder.build()

    assert company.status == "failed"
    assert "403" in company.error_text
    site_client.create_teaser.assert_not_called()


def test_relocate_logo_downloads_and_reuploads_external_url(db_session, site, company):
    """До этого теста builder_logo_src в фикстурах успеха всегда пуст, и
    реальная логика _relocate_logo (скачать внешний логотип, перезалить на
    целевой сайт, обновить builder_logo_src) не выполнялась ни разу."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               city_name="Самара", city_prepositional="Самаре",
                               builder_logo_src="https://yandex.ru/logo.png",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    site_client = Mock(
        create_page=Mock(return_value={"id": 99, "url": "/s/ooo-dom-samara/"}),
        create_teaser=Mock(return_value=555),
        upload_file=Mock(return_value="/media/uploads/service-img/cp-company-7-logo.webp"),
    )
    builder = _builder(db_session, company, site, site_client=site_client)

    fake_response = Mock(content=b"logo-bytes")
    fake_response.raise_for_status = Mock()
    with patch("app.companies.builder.requests.get", return_value=fake_response) as get:
        builder.build()

    get.assert_called_once_with("https://yandex.ru/logo.png", timeout=12)
    site_client.upload_file.assert_called_once_with(
        b"logo-bytes", "cp-company-7-logo.webp", "uploads/service-img/")
    assert company.status == "published"
    assert company.info.builder_logo_src == "/media/uploads/service-img/cp-company-7-logo.webp"


def test_build_finds_logo_on_company_site_when_yandex_data_has_none(db_session, site, company):
    """В выгрузке Яндекс.Карт колонки «Логотип» нет — builder_logo_src у
    CompanyInfo всегда пуст (см. app/api/company_batches.py). Если на сайте
    компании нашёлся логотип, он должен уйти в тот же _relocate_logo — то
    есть в итоге тоже оказаться перезалит в service-img, как и любая другая
    картинка строителя."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               city_name="Самара", city_prepositional="Самаре",
                               builder_logo_src="",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    site_client = Mock(
        create_page=Mock(return_value={"id": 99, "url": "/s/ooo-dom-samara/"}),
        create_teaser=Mock(return_value=555),
        upload_file=Mock(return_value="/media/uploads/service-img/cp-company-7-logo.webp"),
    )
    logo_fn = Mock(return_value="https://dom.ru/static/logo.png")
    builder = _builder(db_session, company, site, site_client=site_client, logo_fn=logo_fn)

    fake_response = Mock(content=b"logo-bytes")
    fake_response.raise_for_status = Mock()
    with patch("app.companies.builder.requests.get", return_value=fake_response) as get:
        builder.build()

    logo_fn.assert_called_once_with("https://dom.ru")
    get.assert_called_once_with("https://dom.ru/static/logo.png", timeout=12)
    site_client.upload_file.assert_called_once_with(
        b"logo-bytes", "cp-company-7-logo.webp", "uploads/service-img/")
    assert company.status == "published"
    assert company.info.builder_logo_src == "/media/uploads/service-img/cp-company-7-logo.webp"


def test_build_does_not_search_company_site_when_yandex_logo_already_present(
        db_session, site, company):
    """Колонка «Логотип» в выгрузке — реже, но встречается; если она уже
    заполнена, повторный обход сайта компании не нужен."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               city_name="Самара", city_prepositional="Самаре",
                               builder_logo_src="https://yandex.ru/logo.png",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    logo_fn = Mock(return_value="https://dom.ru/static/logo.png")
    builder = _builder(db_session, company, site, logo_fn=logo_fn)

    fake_response = Mock(content=b"logo-bytes")
    fake_response.raise_for_status = Mock()
    with patch("app.companies.builder.requests.get", return_value=fake_response):
        builder.build()

    logo_fn.assert_not_called()


def test_build_updates_existing_page_when_remote_page_id_already_set(db_session, site, company):
    """Пересборка компании, у которой страница уже создана (первая сборка
    успела дойти до create_page, но упала позже, например на тизере) —
    обязана PATCH'ить существующую страницу, а не пытаться создать новую
    с тем же детерминированным slug (иначе сайт отвечает HTTP 400
    "страница с таким url уже существует")."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    company.remote_page_id = 99
    company.remote_url = "https://s.ru/s/ooo-dom-samara/"
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               city_name="Самара", city_prepositional="Самаре",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    site_client = Mock(
        update_page_text=Mock(return_value={"id": 99, "url": "/s/ooo-dom-samara/"}),
        create_page=Mock(side_effect=AssertionError("create_page не должен вызываться")),
        create_teaser=Mock(return_value=555),
        upload_file=Mock(),
    )
    builder = _builder(db_session, company, site, site_client=site_client)
    builder.build()

    site_client.update_page_text.assert_called_once()
    assert site_client.update_page_text.call_args.args[0] == 99
    site_client.create_page.assert_not_called()
    assert company.status == "published"
    assert company.remote_page_id == 99


def test_build_updates_existing_teaser_when_teaser_id_already_set(db_session, site, company):
    """Аналогично странице — если тизер уже создан (пересборка уже
    опубликованной компании), нужно обновить именно его, а не плодить
    дубликат."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.commit()
    company.batch_id = company.batch.id
    company.remote_page_id = 99
    company.remote_url = "https://s.ru/s/ooo-dom-samara/"
    company.teaser_id = 555
    db_session.add(company)
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               city_name="Самара", city_prepositional="Самаре",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    site_client = Mock(
        update_page_text=Mock(return_value={"id": 99, "url": "/s/ooo-dom-samara/"}),
        update_teaser=Mock(return_value=555),
        create_page=Mock(side_effect=AssertionError("create_page не должен вызываться")),
        create_teaser=Mock(side_effect=AssertionError("create_teaser не должен вызываться")),
        upload_file=Mock(),
    )
    builder = _builder(db_session, company, site, site_client=site_client)
    builder.build()

    site_client.update_teaser.assert_called_once()
    assert site_client.update_teaser.call_args.args[0] == 555
    site_client.create_teaser.assert_not_called()
    assert company.status == "published"
    assert company.teaser_id == 555
