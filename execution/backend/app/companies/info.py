"""Сборка CompanyInfo из кандидата выгрузки Яндекс.Карт.

Вынесено из app/api/company_batches.py, потому что те же поля нужно уметь
обновлять у уже существующей компании при пересборке (CompanyBuilder), а
импортировать API-модуль в Celery-задачу нельзя.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.company import Company, CompanyCandidate, CompanyInfo
from app.sites.client import normalize_phone


_PHONE_LEAD = re.compile(r"^[\d\s()+\-]+")


def split_phone(raw: str) -> tuple[str, str]:
    """(href, подпись) для блока контактов.

    В выгрузке телефон приходит с пометкой отдела («+7 (495) 106-30-40 Отдел
    продаж», 700 строк из 5214) и иногда несколькими номерами через запятую.
    Раньше сырое значение уходило и в href, и в подпись — на проде 15 из 63
    собранных компаний получили ссылку вида
    `href="tel:+7 (495) 106-30-40 Отдел продаж"` (проверено на живой странице
    stroybaza-moscow.ru/s/akademik-stroy-moskva/). В href должен попадать
    только набираемый номер; пометка остаётся в подписи, она информативна.
    Резать подпись по запятой уместно только когда номер распознан — это и
    подтверждает, что в ячейке список телефонов, а не свободный текст;
    иначе запятая может быть частью фразы, и обрезка теряет её половину.
    Ведущую российскую междугороднюю «8» (как в «8 (800) 333-11-11», 285 из
    5405 распознанных номеров) в href приводим к международной «7» — «+8» не
    код страны, по такой ссылке не дозвониться; подпись при этом не трогаем.
    """
    raw = raw or ""
    first = re.split(r"[,;/|]", raw, maxsplit=1)[0].strip()
    match = _PHONE_LEAD.match(first)
    digits = normalize_phone(match.group(0)) if match else ""
    if digits.startswith("8"):
        digits = "7" + digits[1:]
    text = first if digits else raw.strip()
    return (f"+{digits}" if digits else ""), text


def _fields_from_candidate(candidate: CompanyCandidate) -> dict:
    phone_tel, phone_text = split_phone(candidate.phone)
    contact = {
        "address": candidate.address,
        "phone_tel": phone_tel,
        "phone_text": phone_text,
        "email": candidate.email,
        "working_hours": candidate.working_hours,
        "site_url": candidate.website_raw,
        "site_text": candidate.site_key,
    }
    coordinates = (f"{candidate.lat:.6f}, {candidate.lon:.6f}"
                  if candidate.lat is not None and candidate.lon is not None else "")
    return {
        "builder_name": candidate.name,
        "city_name": candidate.city,
        "builder_logo_src": candidate.logo_url,
        "contacts": [contact] if any(contact.values()) else [],
        "address": candidate.address,
        "coordinates": coordinates,
    }


def build_info_from_candidate(company: Company, candidate: CompanyCandidate) -> CompanyInfo:
    """CompanyInfo с достоверными фактами из выгрузки — это то, что билдер
    (app/companies/builder.py) считает YANDEX_INFO_FIELDS и никогда не даёт
    RouterAI переписывать."""
    return CompanyInfo(company_id=company.id, **_fields_from_candidate(candidate))


def refresh_info_from_candidate(db: Session, company: Company) -> None:
    """Повторно берёт достоверные поля из кандидата перед пересборкой.

    city_prepositional и builder_logo_alt не трогаются: источника у кандидата
    для них нет (первое заполняет модель, см. builder._apply_ai_fields), и
    обновление их бы обнулило.
    """
    if company.candidate_id is None:      # мигрированные из CLI — кандидата нет
        return
    candidate = db.get(CompanyCandidate, company.candidate_id)
    if candidate is None or company.info is None:
        return
    for field, value in _fields_from_candidate(candidate).items():
        # Логотип из выгрузки перекрывает всё; если в выгрузке его нет —
        # оставляем найденный скрейпингом, иначе каждая пересборка заново
        # ходила бы на сайт компании за тем же файлом.
        if field == "builder_logo_src" and not value:
            continue
        setattr(company.info, field, value)
    db.commit()
