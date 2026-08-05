"""Список сайтов для выпадающих списков. Токенов здесь нет ни в каком виде —
менеджеру они не нужны, а лишнее поле в ответе рано или поздно утечёт в лог."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.site import Site
from app.models.user import User

router = APIRouter(prefix="/api/sites", tags=["sites"])


class SiteBrief(BaseModel):
    id: int
    name: str
    domain: str
    publish_target: str
    url_prefix: str
    reference_images: int
    # Готовность считается на бэкенде: у менеджера нет доступа к карточке сайта,
    # и разбираться, чего именно не хватает, — не его задача.
    is_ready: bool


@router.get("", response_model=list[SiteBrief])
def list_sites(db: Session = Depends(get_db),
               _user: User = Depends(get_current_user)) -> list[SiteBrief]:
    sites = db.scalars(select(Site).where(Site.is_active.is_(True)).order_by(Site.name)).all()
    return [
        SiteBrief(
            id=site.id, name=site.name, domain=site.domain,
            publish_target=site.publish_target,
            url_prefix=site.articles_url_prefix,
            reference_images=site.reference_images,
            is_ready=bool(site.articles_url_prefix and site.reference_html
                          and site.reference_images),
        )
        for site in sites
    ]
