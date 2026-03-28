from pydantic import BaseModel
from datetime import datetime
from uuid import UUID


class RulesDocumentResponse(BaseModel):
    id: UUID
    filename: str
    version_label: str
    is_active: bool
    uploaded_at: datetime
    file_size_bytes: int | None = None

    model_config = {"from_attributes": True}


class ActiveRulesResponse(BaseModel):
    document: RulesDocumentResponse | None
    message: str
