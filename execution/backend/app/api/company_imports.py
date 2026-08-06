from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.companies.imports import get_facets, import_file
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
                     status=imp.status, error_message=imp.error_message)


@router.get("/facets", response_model=FacetsOut)
def facets(site_id: int, db: Session = Depends(get_db),
          _user: User = Depends(get_current_user)):
    result = get_facets(db, site_id)
    return FacetsOut(regions=result.regions, categories=result.categories)
