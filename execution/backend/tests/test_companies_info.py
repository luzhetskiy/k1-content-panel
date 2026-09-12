from app.companies.info import (
    build_info_from_candidate, refresh_info_from_candidate, split_phone,
)
from app.models.company import Company, CompanyCandidate, CompanyInfo


def _candidate(**over):
    base = dict(site_key="dom.ru", website_raw="https://dom.ru", name="ООО Дом",
                region_raw="Самарская область", category_raw="Стройка", city="Самара",
                address="ул. Ленина 1", phone="+7 846 000-00-00", email="i@dom.ru",
                working_hours="пн-пт 09:00–18:00", logo_url="https://logo/1.png",
                lat=53.195873, lon=50.100199)
    base.update(over)
    return CompanyCandidate(**base)


def test_build_info_carries_working_hours_and_logo(db_session):
    candidate = _candidate()
    db_session.add(candidate)
    company = Company(site_id=1, site_key="dom.ru", name="ООО Дом")
    db_session.add(company)
    db_session.commit()

    info = build_info_from_candidate(company, candidate)
    assert info.contacts[0]["working_hours"] == "пн-пт 09:00–18:00"
    assert info.builder_logo_src == "https://logo/1.png"
    assert info.coordinates == "53.195873, 50.100199"


def test_refresh_pulls_new_columns_into_existing_info(db_session):
    """Ради этого модуль и заводится: CompanyInfo заполняется один раз при
    создании партии, поэтому у собранных до доработки компаний в contacts
    нет working_hours, а в builder_logo_src — логотипа из выгрузки."""
    candidate = _candidate()
    db_session.add(candidate)
    db_session.flush()
    company = Company(site_id=1, site_key="dom.ru", name="ООО Дом",
                     candidate_id=candidate.id)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    refresh_info_from_candidate(db_session, company)
    assert company.info.contacts[0]["working_hours"] == "пн-пт 09:00–18:00"
    assert company.info.builder_logo_src == "https://logo/1.png"


def test_refresh_keeps_scraped_logo_when_candidate_has_none(db_session):
    """Пустой логотип в выгрузке не должен затирать найденный скрейпингом —
    иначе каждая пересборка заново ходила бы на сайт компании."""
    candidate = _candidate(logo_url="")
    db_session.add(candidate)
    db_session.flush()
    company = Company(site_id=1, site_key="dom.ru", name="ООО Дом",
                     candidate_id=candidate.id)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id,
                               builder_logo_src="/media/uploads/service-img/x.png"))
    db_session.commit()

    refresh_info_from_candidate(db_session, company)
    assert company.info.builder_logo_src == "/media/uploads/service-img/x.png"


def test_refresh_is_noop_for_company_without_candidate(db_session):
    """Мигрированные из CLI компании кандидата не имеют (candidate_id=NULL)."""
    company = Company(site_id=1, site_key="dom.ru", name="ООО Дом", candidate_id=None)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="Старое имя"))
    db_session.commit()

    refresh_info_from_candidate(db_session, company)
    assert company.info.builder_name == "Старое имя"


def test_split_phone_keeps_department_note_out_of_href():
    """Живой дефект: на stroybaza-moscow.ru страница «Академик Строй» отдаёт
    href="tel:+7 (495) 106-30-40 Отдел продаж". Пометка нужна человеку в
    подписи, но в href попадать не должна."""
    href, text = split_phone("+7 (495) 106-30-40 Отдел продаж")
    assert href == "+74951063040"
    assert text == "+7 (495) 106-30-40 Отдел продаж"


def test_split_phone_takes_first_of_several_numbers():
    href, text = split_phone("+7 (495) 157-12-25,+7 (495) 502-79-69 Отдел продаж")
    assert href == "+74951571225"
    assert text == "+7 (495) 157-12-25"


def test_split_phone_returns_empty_href_for_unparseable_value():
    """Номер, который не привести к 10-11 цифрам, — не номер: пустой href
    лучше ссылки, по которой нельзя позвонить."""
    href, text = split_phone("звоните через сайт")
    assert href == ""
    assert text == "звоните через сайт"


def test_split_phone_keeps_free_text_label_intact():
    """Запятая делит номера только тогда, когда в ячейке номера. Если номер
    не распознан, это свободный текст — резать его по запятой значит терять
    половину фразы."""
    href, text = split_phone("звоните с 9 до 18, номер уточняйте на сайте")
    assert href == ""
    assert text == "звоните с 9 до 18, номер уточняйте на сайте"


def test_build_info_splits_phone(db_session):
    candidate = _candidate(phone="+7 (846) 277-06-05 Приёмная")
    db_session.add(candidate)
    company = Company(site_id=1, site_key="dom.ru", name="ООО Дом")
    db_session.add(company)
    db_session.commit()

    info = build_info_from_candidate(company, candidate)
    assert info.contacts[0]["phone_tel"] == "+78462770605"
    assert info.contacts[0]["phone_text"] == "+7 (846) 277-06-05 Приёмная"


def test_refresh_does_not_wipe_city_prepositional(db_session):
    """city_prepositional и builder_logo_alt источника у кандидата не имеют —
    обновление не должно их обнулять."""
    candidate = _candidate()
    db_session.add(candidate)
    db_session.flush()
    company = Company(site_id=1, site_key="dom.ru", name="ООО Дом",
                     candidate_id=candidate.id)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, city_prepositional="Самаре"))
    db_session.commit()

    refresh_info_from_candidate(db_session, company)
    assert company.info.city_prepositional == "Самаре"
