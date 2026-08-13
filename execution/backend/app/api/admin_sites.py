import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.companies.reference import sync_builder_reference
from app.config import config
from app.models.site import Site
from app.models.user import User
from app.settings.crypto import SecretDecryptionError, decrypt, encrypt, mask
from app.sites.client import SiteAPIError, SiteClient
from app.sites.reference import ReferenceError, sync_site_reference

router = APIRouter(prefix="/api/admin/sites", tags=["admin-sites"])

# Три попытки с растущей паузой — то же решение, что и для RouterAI
# (TextClient._call, app/ai/text.py): повторяем 5xx и сетевые сбои, не тратим
# попытки на 4xx, где повтор с тем же запросом гарантированно даёт тот же
# результат (см. "Требование по ретраям" в плане Task 11).
SYNC_MAX_RETRIES = 3
SYNC_RETRY_BACKOFF = 0.5  # секунды; пауза перед следующей попыткой — backoff * 2**attempt


def _sync_is_retryable(exc: SiteAPIError) -> bool:
    """status_code is None — сетевой сбой или сайт вернул не JSON; >= 500 —
    отказ на стороне сайта. Оба класса могут исчезнуть сами при повторе.
    400/401/403/404/413 (неверный токен, нет родительской страницы, файл
    слишком велик, некорректные данные) повторять бессмысленно — та же
    граница, что и _NON_RETRYABLE для RouterAI в app/ai/text.py."""
    return exc.status_code is None or exc.status_code >= 500


class SiteIn(BaseModel):
    name: str
    domain: str
    base_url: str
    api_token: str = ""            # пусто при обновлении = «не менять»
    is_active: bool = True
    site_description: str = ""
    tone_of_voice: str = ""
    publish_target: str = "pages"
    articles_parent_id: int | None = None
    reference_article_id: int | None = None
    image_style_prompt: str = ""
    cover_mode: str = "prompt"
    cover_style_prompt: str = ""
    builder_parent_id: int | None = None
    builder_reference_id: int | None = None


class SiteOut(SiteIn):
    id: int
    watermark_path: str = ""
    # Поля ниже заполняет синхронизация, руками они не редактируются — поэтому
    # их нет в SiteIn: пришедшее с фронта значение всё равно было бы затёрто.
    articles_url_prefix: str = ""
    reference_images: int = 0
    reference_synced_at: datetime | None = None
    builder_reference_synced_at: datetime | None = None


def _to_out(site: Site) -> SiteOut:
    try:
        token = decrypt(site.api_token_enc, config.encryption_key) if site.api_token_enc else ""
        shown = mask(token) if token else ""
    except SecretDecryptionError as exc:
        shown = f"ОШИБКА: {exc}"
    return SiteOut(
        id=site.id, name=site.name, domain=site.domain, base_url=site.base_url,
        api_token=shown, is_active=site.is_active,
        site_description=site.site_description, tone_of_voice=site.tone_of_voice,
        publish_target=site.publish_target, articles_parent_id=site.articles_parent_id,
        reference_article_id=site.reference_article_id,
        image_style_prompt=site.image_style_prompt,
        cover_mode=site.cover_mode, cover_style_prompt=site.cover_style_prompt,
        builder_parent_id=site.builder_parent_id,
        builder_reference_id=site.builder_reference_id,
        watermark_path=site.watermark_path,
        articles_url_prefix=site.articles_url_prefix,
        reference_images=site.reference_images,
        reference_synced_at=site.reference_synced_at,
        builder_reference_synced_at=site.builder_reference_synced_at,
    )


def _apply(site: Site, payload: SiteIn) -> None:
    for field, value in payload.model_dump(exclude={"api_token"}).items():
        setattr(site, field, value)
    if payload.api_token:
        site.api_token_enc = encrypt(payload.api_token, config.encryption_key)


def _get_or_404(db: Session, site_id: int) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(404, "сайт не найден")
    return site


def open_client(db: Session, site: Site) -> SiteClient:
    """Собирает клиент с расшифрованным токеном. Используется и задачами Celery."""
    return SiteClient(site.base_url, decrypt(site.api_token_enc, config.encryption_key))


@router.get("", response_model=list[SiteOut])
def list_sites(db: Session = Depends(get_db),
               _user: User = Depends(require_role("admin", "manager"))):
    return [_to_out(s) for s in db.scalars(select(Site).order_by(Site.name)).all()]


@router.post("", response_model=SiteOut)
def create_site(payload: SiteIn, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin", "manager"))):
    # Site.domain нормализуется валидатором модели (Task 9) при записи, поэтому
    # в базе лежит уже lower/strip. Сравнивать нужно с тем же приведением —
    # иначе "Example.ru" при существующем "example.ru" проскочит эту проверку
    # и упадёт на самом commit() необработанным IntegrityError вместо
    # человеческого 400.
    domain = payload.domain.strip().lower()
    if db.scalars(select(Site).where(Site.domain == domain)).first():
        raise HTTPException(400, f"сайт {domain} уже заведён")
    if not payload.api_token:
        raise HTTPException(400, "токен обязателен при создании сайта")
    site = Site(api_token_enc="")
    _apply(site, payload)
    db.add(site)
    db.commit()
    return _to_out(site)


@router.put("/{site_id}", response_model=SiteOut)
def update_site(site_id: int, payload: SiteIn, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin", "manager"))):
    site = _get_or_404(db, site_id)
    # Та же проверка, что и в create_site, и по той же причине: домен
    # нормализуется валидатором модели, поэтому сравнивать надо нормализованное
    # значение. Без проверки смена домена на уже занятый роняет уникальный
    # индекс необработанным IntegrityError — 500 вместо внятного 400.
    domain = (payload.domain or "").strip().lower()
    clash = db.scalars(
        select(Site).where(Site.domain == domain, Site.id != site.id)).first()
    if clash:
        raise HTTPException(400, f"сайт {domain} уже заведён")
    _apply(site, payload)
    db.commit()
    return _to_out(site)


@router.delete("/{site_id}")
def delete_site(site_id: int, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin", "manager"))):
    db.delete(_get_or_404(db, site_id))
    db.commit()
    return {"ok": True}


class SyncResult(BaseModel):
    ok: bool = True

    articles_ok: bool | None = None   # None = раздел статей не сконфигурирован
    articles_detail: str = ""
    url_prefix: str = ""
    pages: int = 0
    reference_images: int = 0

    builder_ok: bool | None = None    # None = эталон строителя не сконфигурирован
    builder_detail: str = ""


def _sync_with_retries(db: Session, step) -> tuple[bool, str, Any]:
    """Общий цикл ретраев для обоих шагов синхронизации (эталон статьи,
    эталон строителя): 5xx и сетевые сбои (`SiteAPIError.status_code` — `None`
    или `>= 500`) повторяются до SYNC_MAX_RETRIES раз, остальное — сразу
    отказ. `step()` сам делает работу и сам коммитит на успехе — так отказ
    одного шага откатывает только его собственные незакоммиченные изменения,
    не трогая то, что уже успешно закоммитил другой, независимый шаг."""
    for attempt in range(SYNC_MAX_RETRIES):
        try:
            value = step()
        except (ReferenceError, SecretDecryptionError) as exc:
            db.rollback()
            return False, str(exc), None
        except SiteAPIError as exc:
            if not _sync_is_retryable(exc) or attempt == SYNC_MAX_RETRIES - 1:
                db.rollback()
                return False, str(exc), None
            time.sleep(SYNC_RETRY_BACKOFF * (2**attempt))
            continue
        return True, "", value
    return False, "не удалось синхронизировать", None  # недостижимо на практике


@router.post("/{site_id}/sync", response_model=SyncResult)
def sync_site(site_id: int, db: Session = Depends(get_db),
              _user: User = Depends(require_role("admin", "manager"))):
    """Одна кнопка проверяет всё сразу: токен, раздел статей и эталон
    строителя. Два шага независимы — у сайта может быть настроен только один
    из них, и отсутствие настройки для другого не роняет кнопку целиком (см.
    directions/2026-08-13-builder-reference-sync-design.md). Каждый шаг — своя
    транзакция: отказ одного не откатывает уже успешно закоммиченный другой
    (иначе рабочий эталон статей терялся бы из-за отказа синхронизации
    эталона строителя, никак с ним не связанного).

    Ошибки возвращаются телом с articles_ok=False / builder_ok=False, а не
    4xx: это диагностика чужого сайта, а не отказ нашего API — фронту нужно
    показать текст, а не свалиться в общий обработчик ошибок.
    """
    site = _get_or_404(db, site_id)
    result = SyncResult()
    articles_wanted = bool(site.articles_parent_id or site.reference_article_id)
    builder_wanted = bool(site.builder_reference_id)

    if not articles_wanted and not builder_wanted:
        return result

    try:
        client = open_client(db, site)
    except SecretDecryptionError as exc:
        if articles_wanted:
            result.articles_ok = False
            result.articles_detail = str(exc)
        if builder_wanted:
            result.builder_ok = False
            result.builder_detail = str(exc)
        result.ok = False
        return result

    if articles_wanted:
        def articles_step():
            sync_site_reference(db, site, client, commit=False)
            pages = client.list_section_pages(site.articles_url_prefix)
            db.commit()
            return len(pages)

        ok, detail, pages_count = _sync_with_retries(db, articles_step)
        result.articles_ok = ok
        result.articles_detail = detail
        if ok:
            result.url_prefix = site.articles_url_prefix
            result.pages = pages_count
            result.reference_images = site.reference_images

    if builder_wanted:
        def builder_step():
            sync_builder_reference(db, site, client, commit=False)
            db.commit()
            return None

        ok, detail, _value = _sync_with_retries(db, builder_step)
        result.builder_ok = ok
        result.builder_detail = detail

    result.ok = result.articles_ok is not False and result.builder_ok is not False
    return result


@router.post("/{site_id}/watermark")
def upload_watermark(site_id: int, file: UploadFile = File(...),
                     db: Session = Depends(get_db),
                     _user: User = Depends(require_role("admin", "manager"))):
    site = _get_or_404(db, site_id)
    directory = Path(config.media_dir) / "watermarks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{site_id}.png"
    path.write_bytes(file.file.read())
    site.watermark_path = str(path)
    db.commit()
    return {"ok": True, "watermark_path": site.watermark_path}
