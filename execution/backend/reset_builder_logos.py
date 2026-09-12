"""Разовое обнуление builder_logo_src у компаний из партий перед массовой
пересборкой.

_relocate_logo пропускает всё, что начинается с '/', поэтому уже залитые
логотипы (в том числе 9 SVG под именем .webp, которые браузер не рисует) при
пересборке остались бы как есть. После обнуления цепочка «выгрузка →
скрейпинг» разрешает логотип с нуля и заливает с верным расширением.

Мигрированные из CLI компании (candidate_id IS NULL) не трогаются: кандидата
у них нет, заново логотип взять неоткуда.

Запуск (из /app в контейнере api):
    python reset_builder_logos.py
"""

from __future__ import annotations

from app.db import SessionLocal
from app.models.company import Company


def reset_logos(db) -> int:
    companies = db.query(Company).filter(Company.candidate_id.isnot(None)).all()
    reset = 0
    for company in companies:
        if company.info is None or not company.info.builder_logo_src:
            continue
        company.info.builder_logo_src = ""
        reset += 1
    db.commit()
    return reset


def main() -> None:
    db = SessionLocal()
    print(f"обнулено логотипов: {reset_logos(db)}")


if __name__ == "__main__":
    main()
