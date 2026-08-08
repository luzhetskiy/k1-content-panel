import sqlite3
from pathlib import Path

import pytest

from app.models.company import Company, CompanyInfo
from app.models.site import Site
from migrate_companies_from_cli import migrate


@pytest.fixture
def cli_db(tmp_path) -> Path:
    path = tmp_path / "companies.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY, region TEXT, sphere TEXT, name TEXT,
            website TEXT UNIQUE, reviews_count INTEGER DEFAULT 0, rating REAL,
            yandex_url TEXT
        );
        CREATE TABLE company_info (
            company_id INTEGER UNIQUE, builder_name TEXT, city_name TEXT,
            city_prepositional TEXT, builder_logo_src TEXT, builder_logo_alt TEXT,
            about_company TEXT, specialization TEXT, projects_services TEXT,
            benefits TEXT, contacts TEXT, address TEXT, coordinates TEXT
        );
        CREATE TABLE generated_content (
            company_id INTEGER, target_site TEXT, html_content TEXT,
            page_url TEXT, published INTEGER DEFAULT 0
        );
    """)
    conn.execute("INSERT INTO companies (id, region, sphere, name, website, "
                "reviews_count, rating) VALUES (1, 'Самара', 'дома', 'ООО Дом', "
                "'https://dom.ru', 12, 4.7)")
    conn.execute("INSERT INTO company_info (company_id, builder_name, city_name) "
                "VALUES (1, 'ООО Дом', 'Самара')")
    conn.execute("INSERT INTO generated_content (company_id, target_site, page_url, "
                "published) VALUES (1, 'https://vetonit-center.ru', '/s/ooo-dom/', 1)")
    conn.commit()
    conn.close()
    return path


def test_migrate_creates_company_scoped_to_site(db_session, cli_db):
    site = Site(name="Ветонит", domain="vetonit-center.ru", base_url="https://vetonit-center.ru",
               api_token_enc="e")
    db_session.add(site)
    db_session.commit()

    report = migrate(db_session, cli_db)

    assert report.migrated == 1
    assert report.unmatched_sites == []
    company = db_session.query(Company).one()
    assert company.site_id == site.id
    assert company.site_key == "dom.ru"
    assert company.status == "published"
    assert company.info.builder_name == "ООО Дом"


def test_migrate_reports_unmatched_target_site(db_session, cli_db):
    report = migrate(db_session, cli_db)   # ни один Site не заведён
    assert report.migrated == 0
    assert report.unmatched_sites == ["vetonit-center.ru"]


def test_migrate_is_idempotent(db_session, cli_db):
    site = Site(name="Ветонит", domain="vetonit-center.ru", base_url="https://vetonit-center.ru",
               api_token_enc="e")
    db_session.add(site)
    db_session.commit()

    migrate(db_session, cli_db)
    second = migrate(db_session, cli_db)

    assert second.migrated == 0   # уже перенесено — не задваиваем
    assert db_session.query(Company).count() == 1


def test_migrate_sets_category_normalized_from_sphere(db_session, cli_db):
    site = Site(name="Ветонит", domain="vetonit-center.ru", base_url="https://vetonit-center.ru",
               api_token_enc="e")
    db_session.add(site)
    db_session.commit()

    migrate(db_session, cli_db)

    company = db_session.query(Company).one()
    assert company.category_normalized == "дома"
