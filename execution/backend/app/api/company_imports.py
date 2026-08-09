from datetime import datetime

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.companies.imports import get_facets, import_file
from app.models.company import CompanyImport
from app.models.user import User

router = APIRouter(prefix="/api/company-imports", tags=["company-imports"])


class ImportOut(BaseModel):
    id: int
    filename: str
    row_count: int
    matched_count: int
    error_count: int
    status: str
    error_message: str
    uploaded_at: datetime


class FacetsOut(BaseModel):
    regions: list[str]
    categories: list[str]


@router.post("", response_model=ImportOut)
def upload_import(file: UploadFile = File(...), db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    data = file.file.read()
    imp = import_file(db, data, file.filename or "upload.xlsx", uploaded_by_id=user.id)
    return ImportOut(id=imp.id, filename=imp.filename, row_count=imp.row_count,
                     matched_count=imp.matched_count, error_count=imp.error_count,
                     status=imp.status, error_message=imp.error_message,
                     uploaded_at=imp.uploaded_at)


@router.get("", response_model=list[ImportOut])
def list_imports(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    imports = db.scalars(select(CompanyImport).order_by(CompanyImport.uploaded_at.desc())).all()
    return [ImportOut(id=i.id, filename=i.filename, row_count=i.row_count,
                      matched_count=i.matched_count, error_count=i.error_count,
                      status=i.status, error_message=i.error_message,
                      uploaded_at=i.uploaded_at) for i in imports]


@router.get("/facets", response_model=FacetsOut)
def facets(site_id: int, db: Session = Depends(get_db),
          _user: User = Depends(get_current_user)):
    # Несуществующий site_id не даёт 404 — просто ни у одного кандидата нет
    # взятых компаний для него, поэтому возвращается полный пул facets без
    # исключений. Это осознанное поведение, а не недосмотр.
    result = get_facets(db, site_id)
    return FacetsOut(regions=result.regions, categories=result.categories)
