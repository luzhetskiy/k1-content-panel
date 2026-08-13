"""Сборка одной компании: скрейпинг → RouterAI → шаблон → страница →
логотип → тизер. Аналог app/articles/builder.py — падение на любом шаге
переводит компанию в status="failed", а не роняет всю партию."""

from __future__ import annotations

import logging

import requests
from sqlalchemy.orm import Session

from app.ai.factory import build_text_client
from app.ai.prompts import PromptError, render_prompt, resolve_prompt
from app.ai.text import LLMError
from app.companies.logo import fetch_company_logo
from app.companies.scrape import ScrapeError, fetch_company_text
from app.companies.template import fill_builder_template
from app.models.company import YANDEX_INFO_FIELDS, Company, CompanyBatch, CompanyInfo
from app.models.job import LlmUsage
from app.models.site import Site
from app.sites.client import SERVICE_IMG_DIR, SiteAPIError, normalize_phone, slugify

logger = logging.getLogger(__name__)

AI_TEXT_FIELDS = ("about_company", "specialization", "projects_services", "benefits")


def logo_filename(company_id: int) -> str:
    """Префикс cp-company- — та же причина, что и у cp-article- в
    app/articles/builder.py: не пересекаться со старой CLI-схемой
    (execution/step3_fill_template.py грузит логотипы как logo-{name})."""
    return f"cp-company-{company_id}-logo.webp"


def slug_for_company(name: str, city: str) -> str:
    return slugify(f"{name} {city}", limit=60)


class CompanyBuilder:
    def __init__(self, db: Session, company: Company, site: Site, text_client,
                site_client, scrape_fn=fetch_company_text, logo_fn=fetch_company_logo,
                job_run_id: int | None = None):
        self.db = db
        self.company = company
        self.site = site
        self.text_client = text_client
        self.site_client = site_client
        self.scrape_fn = scrape_fn
        self.logo_fn = logo_fn
        self.job_run_id = job_run_id

    def build(self) -> None:
        self.company.status = "generating"
        self.db.commit()
        try:
            self._require_template()
            batch = self._require_batch()
            info = self._require_info()
            scraped_text = self._scrape()
            ai_fields = self._generate_text(info, scraped_text)
            self._apply_ai_fields(info, ai_fields, scraped_text)
            self._find_logo(info)
            self._relocate_logo(info)
            html = fill_builder_template(self.site.builder_template_html, self._info_dict(info))
            page = self._create_page(info, html)
            self._create_teaser(info, page, batch)
        except (ScrapeError, LLMError, PromptError, SiteAPIError) as exc:
            self.db.rollback()
            self.company.status = "failed"
            self.company.error_text = str(exc)
            self.db.commit()
            return
        self.company.status = "published"
        self.company.error_text = ""
        self.db.commit()

    def _require_template(self) -> None:
        if not self.site.builder_template_html:
            raise SiteAPIError(
                "у сайта не задан или не синхронизирован шаблон карточки "
                "строителя — укажи ID эталонной карточки в настройках сайта "
                "и нажми «Проверить и синхронизировать»")

    def _require_batch(self) -> CompanyBatch:
        batch = self.company.batch
        if batch is None:
            raise SiteAPIError(
                "у компании нет партии (batch_id пуст) — карточка-тизер "
                "требует teaser_category_id/teaser_city_id/teaser_location_id, "
                "которые сервис берёт только из партии")
        return batch

    def _require_info(self) -> CompanyInfo:
        info = self.company.info
        if info is None:
            raise SiteAPIError(
                "у компании нет данных Яндекс.Карт (company_info) — "
                "партия создана некорректно")
        return info

    def _scrape(self) -> str:
        return self.scrape_fn(self.company.website)

    def _generate_text(self, info: CompanyInfo, scraped_text: str) -> dict:
        template = resolve_prompt(self.db, "builder_text", self.site.id)
        prompt = render_prompt(template, {
            "company_name": info.builder_name or self.company.name,
            "city": info.city_name or self.company.region,
            "category": self.company.category_normalized,
            "site_name": self.site.name,
            "tone_of_voice": self.site.tone_of_voice,
            "scraped_text": scraped_text,
        })
        result = self.text_client.complete_json(prompt)
        self._record_usage(result.tokens_prompt, result.tokens_completion, result.cost)
        if not isinstance(result.data, dict) or not all(k in result.data for k in AI_TEXT_FIELDS):
            raise LLMError("модель вернула объект без всех текстовых полей")
        return result.data

    def _apply_ai_fields(self, info: CompanyInfo, ai_fields: dict, scraped_text: str) -> None:
        # YANDEX_INFO_FIELDS не трогаются — только четыре текстовых поля.
        for field in AI_TEXT_FIELDS:
            setattr(info, field, ai_fields[field])
        # Сырой текст сайта — для отладки качества промпта, аналог raw_html
        # в старой схеме (execution/db.py). Ничего не читает его обратно.
        info.scraped_text = scraped_text
        self.db.commit()

    def _find_logo(self, info: CompanyInfo) -> None:
        """В выгрузке Яндекс.Карт колонки «Логотип» нет — builder_logo_src
        пуст почти всегда. Ищем логотип в шапке сайта компании (см.
        app/companies/logo.py); найденный внешний URL уходит в тот же
        _relocate_logo, что и логотип из выгрузки — то есть тоже
        перезаливается в service-img."""
        if info.builder_logo_src:
            return
        logo_url = self.logo_fn(self.company.website)
        if logo_url:
            info.builder_logo_src = logo_url
            self.db.commit()

    def _relocate_logo(self, info: CompanyInfo) -> None:
        """Внешний логотип перезаливается на целевой сайт — иначе карточка
        зависит от чужого хостинга. Уже локальные пути (/media/...) не трогаем."""
        if not info.builder_logo_src or info.builder_logo_src.startswith("/"):
            return
        try:
            response = requests.get(info.builder_logo_src, timeout=12)
            response.raise_for_status()
        except requests.RequestException:
            logger.warning(
                "не удалось перезалить логотип компании %s (%s) — оставляю "
                "внешнюю ссылку как есть", self.company.id, info.builder_logo_src)
            return
        filename = logo_filename(self.company.id)
        info.builder_logo_src = self.site_client.upload_file(
            response.content, filename, SERVICE_IMG_DIR)
        self.db.commit()

    def _info_dict(self, info: CompanyInfo) -> dict:
        return {f: getattr(info, f) for f in YANDEX_INFO_FIELDS + AI_TEXT_FIELDS}

    def _create_page(self, info: CompanyInfo, html: str) -> dict:
        """Пересборка (Company.remote_page_id уже задан — либо компания уже
        опубликована, либо первая сборка успела создать страницу и упала
        позже) обновляет существующую страницу вместо создания новой: slug
        детерминирован из имени+города и не меняется между сборками, так что
        повторный create_page бился бы в HTTP 400 "страница с таким url уже
        существует"."""
        if self.company.remote_page_id:
            page = self.site_client.update_page_text(self.company.remote_page_id, html)
            self.company.remote_page_id = page.get("id", self.company.remote_page_id)
            if page.get("url"):
                self.company.remote_url = f"{self.site.base_url}{page['url']}"
        else:
            name = info.builder_name or self.company.name
            city = info.city_name or self.company.region
            slug = slug_for_company(name, city)
            page = self.site_client.create_page(
                title=f"{name} — {self.company.category_normalized} в {city}",
                url=f"/s/{slug}/", html=html, parent_id=self.site.builder_parent_id,
                meta_description=f"{name} — {self.company.category_normalized} в {city}. "
                                 f"Контакты, услуги, отзывы.",
            )
            self.company.remote_page_id = page["id"]
            self.company.remote_url = f"{self.site.base_url}{page.get('url', '')}"
        self.db.commit()
        return page

    def _create_teaser(self, info: CompanyInfo, page: dict, batch: CompanyBatch) -> None:
        contacts = info.contacts or [{}]
        contact = contacts[0]
        kwargs = dict(
            name=info.builder_name or self.company.name,
            slug=page.get("url", "").removeprefix("/s/").rstrip("/"),
            address=contact.get("address", "") or info.address,
            phone=normalize_phone(contact.get("phone_tel", "")), email=contact.get("email", ""),
            website=self.company.website, page_url=page.get("url", ""),
            category=batch.teaser_category_id, city=batch.teaser_city_id,
            location=batch.teaser_location_id,
        )
        # Пересборка (Company.teaser_id уже задан) обновляет существующий
        # тизер вместо создания дубликата — та же причина, что и у
        # _create_page выше.
        if self.company.teaser_id:
            teaser_id = self.site_client.update_teaser(self.company.teaser_id, **kwargs)
        else:
            teaser_id = self.site_client.create_teaser(**kwargs)
        self.company.teaser_id = teaser_id
        self.db.commit()

    def _record_usage(self, tokens_prompt: int, tokens_completion: int, cost: float) -> None:
        if self.job_run_id is None:
            return
        self.db.add(LlmUsage(job_run_id=self.job_run_id, kind="text",
                             model=getattr(self.text_client, "model", ""),
                             tokens_prompt=tokens_prompt,
                             tokens_completion=tokens_completion, cost=cost))
        self.db.commit()


def build_for(db: Session, company: Company, site: Site, site_client,
             job_run_id: int | None) -> None:
    CompanyBuilder(db=db, company=company, site=site,
                   text_client=build_text_client(db), site_client=site_client,
                   job_run_id=job_run_id).build()
