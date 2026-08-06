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
