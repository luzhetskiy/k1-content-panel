from app.models.company import CompanyCandidate, CompanyImport


def test_company_import_defaults(db_session):
    imp = CompanyImport(filename="builders.xlsx", row_count=100)
    db_session.add(imp)
    db_session.commit()
    assert imp.status == "parsed"
    assert imp.matched_count == 0
    assert imp.error_count == 0


def test_company_candidate_site_key_is_unique(db_session):
    from sqlalchemy.exc import IntegrityError

    db_session.add(CompanyCandidate(site_key="stroyka.ru", name="ООО Стройка"))
    db_session.commit()
    db_session.add(CompanyCandidate(site_key="stroyka.ru", name="Дубль"))
    try:
        db_session.commit()
        assert False, "ожидался IntegrityError на повторном site_key"
    except IntegrityError:
        db_session.rollback()


from app.models.company import Company, CompanyBatch, CompanyInfo


def test_company_batch_starts_in_selection_review(db_session):
    batch = CompanyBatch(site_id=1, region_raw="Самара", category_raw="Строительство домов",
                         category_normalized="Дома под ключ", requested_count=10)
    db_session.add(batch)
    db_session.commit()
    assert batch.status == "selection_review"


def test_company_unique_per_site_and_site_key(db_session):
    from sqlalchemy.exc import IntegrityError

    db_session.add(Company(site_id=1, site_key="stroyka.ru", name="А"))
    db_session.commit()
    db_session.add(Company(site_id=1, site_key="stroyka.ru", name="Б"))
    try:
        db_session.commit()
        assert False, "ожидался IntegrityError на дубле (site_id, site_key)"
    except IntegrityError:
        db_session.rollback()


def test_same_site_key_allowed_on_different_sites(db_session):
    db_session.add_all([
        Company(site_id=1, site_key="stroyka.ru", name="А"),
        Company(site_id=2, site_key="stroyka.ru", name="А"),
    ])
    db_session.commit()   # не должно бросить IntegrityError


def test_company_info_one_to_one(db_session):
    company = Company(site_id=1, site_key="stroyka.ru", name="А")
    db_session.add(company)
    db_session.commit()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Стройка"))
    db_session.commit()
    db_session.refresh(company)
    assert company.info.builder_name == "ООО Стройка"
