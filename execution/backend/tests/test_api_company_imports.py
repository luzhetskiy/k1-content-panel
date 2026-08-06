import io

import openpyxl


def _wb_bytes(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Название", "Категории", "Регион", "Город", "Сайт", "Оценок", "Отзывов", "Рейтинг"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_manager_uploads_import(manager_client):
    data = _wb_bytes([["ООО Дом", "Дома", "Самара", "Самара", "https://dom.ru", 5, 3, 4.5]])
    resp = manager_client.post(
        "/api/company-imports",
        files={"file": ("builders.xlsx", data,
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "parsed"
    assert body["matched_count"] == 1


def test_upload_rejects_broken_file(manager_client):
    resp = manager_client.post(
        "/api/company-imports",
        files={"file": ("bad.xlsx", b"not an xlsx", "application/octet-stream")})
    assert resp.status_code == 200   # ошибка парсинга — это статус, не HTTP-код
    assert resp.json()["status"] == "failed"


def test_facets_endpoint_requires_site_id(manager_client, db_session):
    from app.models.site import Site

    site = Site(name="С", domain="s.ru", base_url="https://s.ru", api_token_enc="e")
    db_session.add(site)
    db_session.commit()

    data = _wb_bytes([["ООО Дом", "Дома", "Самара", "Самара", "https://dom.ru", 5, 3, 4.5]])
    manager_client.post("/api/company-imports",
                        files={"file": ("b.xlsx", data, "application/octet-stream")})

    resp = manager_client.get(f"/api/company-imports/facets?site_id={site.id}")
    assert resp.status_code == 200
    assert resp.json() == {"regions": ["Самара"], "categories": ["Дома"]}
