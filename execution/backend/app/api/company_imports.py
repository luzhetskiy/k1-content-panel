from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.companies.imports import get_facets, import_file
from app.config import config
from app.models.company import CompanyCandidate, CompanyImport
from app.models.user import User

router = APIRouter(prefix="/api/company-imports", tags=["company-imports"])


class ImportOut(BaseModel):
    id: int
    filename: str
    row_count: int
    matched_count: int
    new_count: int | None            # None — загрузка до того, как это начали считать
    error_count: int
    status: str
    error_message: str
    uploaded_at: datetime
    uploaded_by: str
    has_file: bool


class SummaryOut(BaseModel):
    total_candidates: int


class FacetsOut(BaseModel):
    regions: list[str]
    categories: list[str]


def _to_out(db: Session, imp: CompanyImport) -> ImportOut:
    uploader = db.get(User, imp.uploaded_by_id) if imp.uploaded_by_id is not None else None
    return ImportOut(id=imp.id, filename=imp.filename, row_count=imp.row_count,
                     matched_count=imp.matched_count, new_count=imp.new_count,
                     error_count=imp.error_count, status=imp.status,
                     error_message=imp.error_message, uploaded_at=imp.uploaded_at,
                     uploaded_by=uploader.full_name if uploader else "",
                     has_file=bool(imp.stored_path) and Path(imp.stored_path).is_file())


@router.post("", response_model=ImportOut)
def upload_import(file: UploadFile = File(...), db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    data = file.file.read()
    imp = import_file(db, data, file.filename or "upload.xlsx", uploaded_by_id=user.id)
    # Сохраняем и упавшие загрузки: по файлу видно, что с ним было не так.
    directory = Path(config.media_dir) / "company-imports"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{imp.id}.xlsx"
    path.write_bytes(data)
    imp.stored_path = str(path)
    db.commit()
    return _to_out(db, imp)


@router.get("", response_model=list[ImportOut])
def list_imports(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    imports = db.scalars(select(CompanyImport).order_by(CompanyImport.uploaded_at.desc())).all()
    return [_to_out(db, i) for i in imports]


@router.get("/summary", response_model=SummaryOut)
def summary(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    total = db.scalar(select(func.count()).select_from(CompanyCandidate)) or 0
    return SummaryOut(total_candidates=total)


@router.get("/facets", response_model=FacetsOut)
def facets(site_id: int, db: Session = Depends(get_db),
          _user: User = Depends(get_current_user)):
    # Несуществующий site_id не даёт 404 — просто ни у одного кандидата нет
    # взятых компаний для него, поэтому возвращается полный пул facets без
    # исключений. Это осознанное поведение, а не недосмотр.
    result = get_facets(db, site_id)
    return FacetsOut(regions=result.regions, categories=result.categories)


@router.get("/{import_id}/file")
def download_file(import_id: int, db: Session = Depends(get_db),
                  _user: User = Depends(get_current_user)):
    imp = db.get(CompanyImport, import_id)
    if imp is None or not imp.stored_path or not Path(imp.stored_path).is_file():
        raise HTTPException(404, "файл этой загрузки не сохранился")
    return FileResponse(
        imp.stored_path, filename=imp.filename or f"import-{imp.id}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
