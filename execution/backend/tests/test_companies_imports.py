import io

import openpyxl

from app.companies.imports import get_facets, import_file
from app.models.company import Company, CompanyCandidate


def _wb_bytes(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Название", "Категории", "Регион", "Город", "Сайт", "Оценок", "Отзывов", "Рейтинг"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_import_creates_candidates(db_session):
    data = _wb_bytes([["ООО Дом", "Дома", "Самара", "Самара", "https://dom.ru", 5, 3, 4.5]])
    imp = import_file(db_session, data, "builders.xlsx", uploaded_by_id=None)
    assert imp.row_count == 1
    assert imp.matched_count == 1
    candidates = db_session.query(CompanyCandidate).all()
    assert len(candidates) == 1
    assert candidates[0].site_key == "dom.ru"


def test_reimport_upserts_existing_candidate(db_session):
    first = _wb_bytes([["ООО Дом", "Дома", "Самара", "Самара", "https://dom.ru", 5, 3, 4.5]])
    import_file(db_session, first, "builders.xlsx", uploaded_by_id=None)

    second = _wb_bytes([["ООО Дом", "Дома", "Самара", "Самара", "https://dom.ru", 20, 15, 4.9]])
    import_file(db_session, second, "builders2.xlsx", uploaded_by_id=None)

    candidates = db_session.query(CompanyCandidate).all()
    assert len(candidates) == 1
    assert candidates[0].reviews_count == 15
    assert candidates[0].rating == 4.9


def test_facets_lists_distinct_region_and_category(db_session):
    data = _wb_bytes([
        ["ООО Дом", "Дома", "Самара", "Самара", "https://dom1.ru", 1, 1, 4.0],
        ["ООО Дом2", "Бани", "Москва", "Москва", "https://dom2.ru", 1, 1, 4.0],
    ])
    import_file(db_session, data, "builders.xlsx", uploaded_by_id=None)
    facets = get_facets(db_session, site_id=1)
    assert set(facets.regions) == {"Самара", "Москва"}
    assert set(facets.categories) == {"Дома", "Бани"}


def test_facets_excludes_pairs_fully_taken_for_site(db_session):
    data = _wb_bytes([["ООО Дом", "Дома", "Самара", "Самара", "https://dom.ru", 1, 1, 4.0]])
    import_file(db_session, data, "builders.xlsx", uploaded_by_id=None)
    candidate = db_session.query(CompanyCandidate).one()
    db_session.add(Company(site_id=1, site_key=candidate.site_key, name="ООО Дом"))
    db_session.commit()

    facets = get_facets(db_session, site_id=1)
    assert "Дома" not in facets.categories
