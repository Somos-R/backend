from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.domains.catalogs.models import DocumentType


def is_document_type_active(db: Session, code: str) -> bool:
    document_type = db.get(DocumentType, code)
    return document_type is not None and document_type.is_active


def ensure_document_type_is_active(db: Session, code: str) -> None:
    """An unknown or deactivated document type cannot be used for a new account or seller."""
    if not is_document_type_active(db, code):
        raise ApiError("invalid_id_type", status_code=422, detail=f"id_type '{code}' no es válido")
