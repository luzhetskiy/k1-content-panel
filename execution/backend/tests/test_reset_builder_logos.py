from reset_builder_logos import reset_logos
from app.models.company import Company, CompanyInfo


def test_reset_clears_logos_of_batch_companies(db_session):
    company = Company(site_id=1, batch_id=1, candidate_id=1, site_key="dom.ru",
                     name="ООО Дом")
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id,
                               builder_logo_src="/media/uploads/service-img/x.webp"))
    db_session.commit()

    assert reset_logos(db_session) == 1
    assert company.info.builder_logo_src == ""


def test_reset_skips_companies_migrated_from_cli(db_session):
    """У мигрированных из CLI компаний кандидата нет: обнулив логотип, мы
    оставили бы их карточки вообще без картинки — пересобрать их нечем."""
    company = Company(site_id=1, batch_id=None, candidate_id=None, site_key="old.ru",
                     name="Старая")
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id,
                               builder_logo_src="https://old.ru/logo.png"))
    db_session.commit()

    assert reset_logos(db_session) == 0
    assert company.info.builder_logo_src == "https://old.ru/logo.png"
