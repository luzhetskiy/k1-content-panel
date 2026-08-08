"""Разовая миграция execution/data/companies.db (CLI, SQLite) → Postgres.

Запуск: python migrate_companies_from_cli.py [--file путь/к/companies.db]

Критично выполнить до включения раздела «Строители» в проде: без неё дедуп
по (site_id, site_key) не увидит уже опубликованные вручную компании — см.
directions/2026-08-06-builders-import-design.md §6.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.companies.import_xlsx import site_key as normalize_site_key
from app.db import SessionLocal
from app.models.company import Company, CompanyInfo
from app.models.site import Site

DEFAULT_CLI_DB = Path(__file__).parent.parent / "data" / "companies.db"


@dataclass
class MigrationReport:
    migrated: int = 0
    unmatched_sites: list[str] = field(default_factory=list)


def _site_domain_from_target(target_site: str) -> str:
    return urlparse(target_site).netloc.lower().removeprefix("www.")


def migrate(db: Session, cli_db_path: Path) -> MigrationReport:
    report = MigrationReport()
    conn = sqlite3.connect(cli_db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT c.id, c.region, c.sphere, c.name, c.website, c.reviews_count, c.rating,
                   c.yandex_url, gc.target_site, gc.page_url, gc.published,
                   ci.builder_name, ci.city_name, ci.city_prepositional,
                   ci.builder_logo_src, ci.builder_logo_alt, ci.about_company,
                   ci.specialization, ci.projects_services, ci.benefits,
                   ci.contacts, ci.address, ci.coordinates
            FROM companies c
            JOIN generated_content gc ON gc.company_id = c.id
            LEFT JOIN company_info ci ON ci.company_id = c.id
        """).fetchall()

        unmatched: set[str] = set()
        for row in rows:
            domain = _site_domain_from_target(row["target_site"])
            site = db.scalars(select(Site).where(Site.domain == domain)).first()
            if site is None:
                unmatched.add(domain)
                continue

            key = normalize_site_key(row["website"])
            existing = db.scalars(
                select(Company).where(Company.site_id == site.id, Company.site_key == key)
            ).first()
            if existing is not None:
                continue   # уже перенесено — идемпотентность

            company = Company(
                site_id=site.id, site_key=key, website=row["website"], name=row["name"],
                region=row["region"] or "", category_normalized=row["sphere"] or "",
                rating=row["rating"], reviews_count=row["reviews_count"] or 0,
                yandex_url=row["yandex_url"] or "",
                # status по наличию page_url, а не по published: в реальных
                # данных CLI (execution/db.py, update_page_url) published
                # всегда пишется как 0 — мёртвая колонка, не сигнал. page_url
                # надёжно отличает реально опубликованные страницы.
                status="published" if row["page_url"] else "failed",
                remote_url=row["page_url"] or "",
                error_text="" if row["page_url"] else "перенесено из CLI без готовой страницы",
            )
            db.add(company)
            db.flush()

            contacts = row["contacts"]
            try:
                contacts = json.loads(contacts) if contacts else []
            except (TypeError, json.JSONDecodeError):
                contacts = []

            db.add(CompanyInfo(
                company_id=company.id, builder_name=row["builder_name"] or "",
                city_name=row["city_name"] or "", city_prepositional=row["city_prepositional"] or "",
                builder_logo_src=row["builder_logo_src"] or "",
                builder_logo_alt=row["builder_logo_alt"] or "",
                about_company=row["about_company"] or "", specialization=row["specialization"] or "",
                projects_services=row["projects_services"] or "", benefits=row["benefits"] or "",
                contacts=contacts, address=row["address"] or "", coordinates=row["coordinates"] or "",
            ))
            report.migrated += 1

        report.unmatched_sites = sorted(unmatched)
    finally:
        conn.close()

    db.commit()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Миграция companies.db → Postgres")
    parser.add_argument("--file", default=str(DEFAULT_CLI_DB))
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Файл не найден: {path}")

    db = SessionLocal()
    try:
        report = migrate(db, path)
    finally:
        db.close()

    print(f"Перенесено компаний: {report.migrated}")
    if report.unmatched_sites:
        print(f"ВНИМАНИЕ: не найден Site для доменов (заведи их в /admin/sites — "
             f"проверь также, не отличается ли домен наличием www. — и запусти "
             f"скрипт повторно, миграция идемпотентна): "
             f"{', '.join(report.unmatched_sites)}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
