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


def _builder(db, company, site, text_client=None, site_client=None, scrape=None):
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
