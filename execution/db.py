"""SQLite helper for k1-parser-services."""

import json
import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "companies.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                region TEXT NOT NULL,
                sphere TEXT NOT NULL,
                name TEXT NOT NULL,
                website TEXT UNIQUE NOT NULL,
                traffic_estimate INTEGER DEFAULT 0,
                citations_count INTEGER DEFAULT 0,
                reviews_count INTEGER DEFAULT 0,
                rating REAL,
                yandex_url TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS company_info (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER UNIQUE REFERENCES companies(id),
                builder_name TEXT,
                city_name TEXT,
                city_prepositional TEXT,
                builder_label TEXT,
                builder_logo_src TEXT,
                builder_logo_alt TEXT,
                builder_logo_svg TEXT,
                builder_background_src TEXT,
                builder_short_description TEXT,
                builder_main_title TEXT,
                about_company TEXT,
                specialization TEXT,
                projects_services TEXT,
                benefits TEXT,
                contacts TEXT,
                address TEXT,
                coordinates TEXT,
                raw_html TEXT,
                scraped_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS generated_content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER REFERENCES companies(id),
                target_site TEXT NOT NULL,
                html_content TEXT,
                verified INTEGER DEFAULT 0,
                verification_notes TEXT,
                page_url TEXT,
                published INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(company_id, target_site)
            );
        """)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Добавляет недостающие колонки в уже существующих БД (idempotent)."""
    migrations = {
        "companies": {
            "rating": "REAL",
            "yandex_url": "TEXT",
        },
        "company_info": {
            "builder_logo_svg": "TEXT",
        },
    }
    for table, columns in migrations.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, col_type in columns.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")


def upsert_company(
    region: str,
    sphere: str,
    name: str,
    website: str,
    traffic_estimate: int = 0,
    citations_count: int = 0,
    reviews_count: int = 0,
    rating: Optional[float] = None,
    yandex_url: Optional[str] = None,
) -> int:
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO companies (region, sphere, name, website, traffic_estimate,
                                   citations_count, reviews_count, rating, yandex_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(website) DO UPDATE SET
                name=excluded.name,
                traffic_estimate=MAX(traffic_estimate, excluded.traffic_estimate),
                citations_count=MAX(citations_count, excluded.citations_count),
                reviews_count=MAX(reviews_count, excluded.reviews_count),
                rating=COALESCE(excluded.rating, rating),
                yandex_url=COALESCE(excluded.yandex_url, yandex_url),
                updated_at=datetime('now')
        """, (region, sphere, name, website, traffic_estimate, citations_count,
              reviews_count, rating, yandex_url))
        row = conn.execute("SELECT id FROM companies WHERE website=?", (website,)).fetchone()
        return row["id"]


# Поля company_info, которые заполняются достоверными данными из выгрузки Яндекс.Карт.
# Скрейпинг сайта (шаг 2) их НЕ трогает — только маркетинговый текст.
YANDEX_INFO_FIELDS = (
    "builder_name", "city_name", "city_prepositional",
    "builder_logo_src", "builder_logo_alt",
    "contacts", "address", "coordinates",
)


def save_yandex_info(company_id: int, info: dict) -> None:
    """Записывает в company_info только достоверные поля из файла Яндекс.Карт.

    Использует upsert по company_id и обновляет исключительно колонки из
    YANDEX_INFO_FIELDS, поэтому маркетинговый текст, добавленный скрейпингом,
    не затирается при повторном импорте.
    """
    contacts = info.get("contacts")
    if isinstance(contacts, (list, dict)):
        contacts = json.dumps(contacts, ensure_ascii=False)

    values = {
        "builder_name": info.get("builder_name"),
        "city_name": info.get("city_name"),
        "city_prepositional": info.get("city_prepositional"),
        "builder_logo_src": info.get("builder_logo_src"),
        "builder_logo_alt": info.get("builder_logo_alt"),
        "contacts": contacts,
        "address": info.get("address"),
        "coordinates": info.get("coordinates"),
    }

    columns = ["company_id"] + list(values.keys())
    placeholders = ", ".join("?" for _ in columns)
    update_clause = ", ".join(f"{k}=excluded.{k}" for k in values)

    with get_connection() as conn:
        conn.execute(
            f"""
            INSERT INTO company_info ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(company_id) DO UPDATE SET {update_clause}
            """,
            [company_id] + list(values.values()),
        )


def get_companies(region: Optional[str] = None, sphere: Optional[str] = None, limit: int = 50,
                  only_without_info: bool = False, only_without_text: bool = False) -> list:
    query = "SELECT c.* FROM companies c"
    params = []
    conditions = []

    if only_without_info:
        query += " LEFT JOIN company_info ci ON c.id = ci.company_id"
        conditions.append("ci.id IS NULL")
    elif only_without_text:
        # Импорт из Яндекса создаёт company_info с контактами, но без текста.
        # Отбираем компании, которым скрейпинг ещё не заполнил about_company.
        query += " LEFT JOIN company_info ci ON c.id = ci.company_id"
        conditions.append("(ci.id IS NULL OR ci.about_company IS NULL OR ci.about_company = '')")
    if region:
        conditions.append("c.region = ?")
        params.append(region)
    if sphere:
        conditions.append("c.sphere = ?")
        params.append(sphere)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY c.citations_count DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def save_company_info(company_id: int, info: dict) -> None:
    contacts = info.get("contacts")
    if isinstance(contacts, list):
        contacts = json.dumps(contacts, ensure_ascii=False)

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO company_info (
                company_id, builder_name, city_name, city_prepositional, builder_label,
                builder_logo_src, builder_logo_alt, builder_background_src,
                builder_short_description, builder_main_title,
                about_company, specialization, projects_services, benefits,
                contacts, address, coordinates, raw_html
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(company_id) DO UPDATE SET
                builder_name=excluded.builder_name,
                city_name=excluded.city_name,
                city_prepositional=excluded.city_prepositional,
                builder_label=excluded.builder_label,
                builder_logo_src=excluded.builder_logo_src,
                builder_logo_alt=excluded.builder_logo_alt,
                builder_background_src=excluded.builder_background_src,
                builder_short_description=excluded.builder_short_description,
                builder_main_title=excluded.builder_main_title,
                about_company=excluded.about_company,
                specialization=excluded.specialization,
                projects_services=excluded.projects_services,
                benefits=excluded.benefits,
                contacts=excluded.contacts,
                address=excluded.address,
                coordinates=excluded.coordinates,
                raw_html=excluded.raw_html,
                scraped_at=datetime('now')
        """, (
            company_id,
            info.get("builder_name"),
            info.get("city_name"),
            info.get("city_prepositional"),
            info.get("builder_label"),
            info.get("builder_logo_src"),
            info.get("builder_logo_alt"),
            info.get("builder_background_src"),
            info.get("builder_short_description"),
            info.get("builder_main_title"),
            info.get("about_company"),
            info.get("specialization"),
            info.get("projects_services"),
            info.get("benefits"),
            contacts,
            info.get("address"),
            info.get("coordinates"),
            info.get("raw_html"),
        ))


def get_company_info(company_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM company_info WHERE company_id=?", (company_id,)
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    if data.get("contacts"):
        try:
            data["contacts"] = json.loads(data["contacts"])
        except json.JSONDecodeError:
            pass
    return data


def save_generated_content(company_id: int, target_site: str, html_content: str) -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO generated_content (company_id, target_site, html_content)
            VALUES (?, ?, ?)
            ON CONFLICT(company_id, target_site) DO UPDATE SET
                html_content=excluded.html_content,
                verified=0,
                verification_notes=NULL,
                created_at=datetime('now')
        """, (company_id, target_site, html_content))


def get_generated_content(company_id: int, target_site: str = None) -> list:
    query = "SELECT * FROM generated_content WHERE company_id=?"
    params = [company_id]
    if target_site:
        query += " AND target_site=?"
        params.append(target_site)
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def update_verification(company_id: int, target_site: str, verified: bool, notes: str) -> None:
    with get_connection() as conn:
        conn.execute("""
            UPDATE generated_content
            SET verified=?, verification_notes=?
            WHERE company_id=? AND target_site=?
        """, (int(verified), notes, company_id, target_site))


def update_page_url(company_id: int, target_site: str, page_url: str) -> None:
    with get_connection() as conn:
        conn.execute("""
            UPDATE generated_content
            SET page_url=?, published=0
            WHERE company_id=? AND target_site=?
        """, (page_url, company_id, target_site))


def patch_company_info(company_id: int, fields: dict) -> None:
    """Обновляет только указанные поля в company_info."""
    if not fields:
        return
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [company_id]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE company_info SET {set_clause} WHERE company_id=?",
            values,
        )


def query_companies_no_logo(sphere: Optional[str] = None) -> list:
    """Компании без builder_logo_src и builder_logo_svg."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT c.id, c.website, ci.builder_name
            FROM companies c
            JOIN company_info ci ON ci.company_id = c.id
            WHERE (ci.builder_logo_src IS NULL OR ci.builder_logo_src = '')
              AND (ci.builder_logo_svg IS NULL OR ci.builder_logo_svg = '')
              AND (? IS NULL OR c.sphere = ?)
            ORDER BY c.id
        """, (sphere, sphere)).fetchall()
    return [dict(r) for r in rows]
