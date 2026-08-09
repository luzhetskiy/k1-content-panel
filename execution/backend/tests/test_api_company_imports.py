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


def test_upload_requires_auth(client):
    resp = client.post("/api/company-imports", files={"file": ("b.xlsx", b"x", "application/octet-stream")})
    assert resp.status_code == 401


def test_facets_requires_auth(client):
    resp = client.get("/api/company-imports/facets?site_id=1")
    assert resp.status_code == 401


def test_list_imports_returns_newest_first(manager_client):
    data1 = _wb_bytes([["ООО Дом", "Дома", "Самара", "Самара", "https://dom.ru", 5, 3, 4.5]])
    resp1 = manager_client.post(
        "/api/company-imports",
        files={"file": ("first.xlsx", data1,
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert resp1.status_code == 200

    data2 = _wb_bytes([
        ["ООО Дача", "Дачи", "Москва", "Москва", "https://dacha.ru", 2, 1, 4.0],
        ["ООО Баня", "Бани", "Москва", "Москва", "https://banya.ru", 7, 4, 4.8],
    ])
    resp2 = manager_client.post(
        "/api/company-imports",
        files={"file": ("second.xlsx", data2,
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert resp2.status_code == 200

    body1 = resp1.json()
    body2 = resp2.json()

    resp = manager_client.get("/api/company-imports")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2

    assert items[0]["id"] == body2["id"]
    assert items[0]["filename"] == "second.xlsx"
    assert items[0]["row_count"] == 2
    assert items[0]["matched_count"] == 2
    assert "uploaded_at" in items[0] and items[0]["uploaded_at"]

    assert items[1]["id"] == body1["id"]
    assert items[1]["filename"] == "first.xlsx"
    assert items[1]["row_count"] == 1
    assert items[1]["matched_count"] == 1
    assert "uploaded_at" in items[1] and items[1]["uploaded_at"]


def test_list_imports_requires_auth(client):
    resp = client.get("/api/company-imports")
    assert resp.status_code == 401


def test_list_imports_empty_when_none_uploaded(manager_client):
    resp = manager_client.get("/api/company-imports")
    assert resp.status_code == 200
    assert resp.json() == []
