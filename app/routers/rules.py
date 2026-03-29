import io
import logging
from datetime import datetime, timezone
from typing import Annotated

import pdfplumber
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_db, require_admin
from app.models.rules_document import RulesDocument
from app.models.usage_event import EventType, UsageEvent
from app.models.user import User
from app.schemas.rules import ActiveRulesResponse, RulesDocumentResponse
from app.services.ai_service import ai_service
from app.services.storage_service import storage_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rules"])


def _log_event(db: Session, user_id: str, event_type: EventType, metadata: dict | None = None) -> None:
    event = UsageEvent(user_id=user_id, event_type=event_type, event_metadata=metadata)
    db.add(event)
    db.commit()


@router.get("/rules/active", response_model=ActiveRulesResponse)
def get_active_rules(
    _current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ActiveRulesResponse:
    document = (
        db.query(RulesDocument)
        .filter(RulesDocument.is_active == True)  # noqa: E712
        .order_by(RulesDocument.uploaded_at.desc())
        .first()
    )
    if not document:
        return ActiveRulesResponse(document=None, message="No active rules document found")
    return ActiveRulesResponse(
        document=RulesDocumentResponse.model_validate(document),
        message="Active rules document retrieved",
    )


@router.get("/rules/download")
def download_pdf(
    current_user: Annotated[User, Depends(get_current_user)],
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    document = (
        db.query(RulesDocument)
        .filter(RulesDocument.is_active == True)  # noqa: E712
        .order_by(RulesDocument.uploaded_at.desc())
        .first()
    )
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active rules document available")

    try:
        pdf_bytes = storage_service.get_file_bytes(document.storage_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    background_tasks.add_task(
        _log_event,
        db,
        str(current_user.id),
        EventType.pdf_view,
        {"document_id": str(document.id), "filename": document.filename},
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{document.filename}"'},
    )


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str


@router.post("/rules/ask", response_model=AskResponse)
def ask_rules_question(
    body: AskRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
) -> AskResponse:
    """Ask a question about the rulebook using AI."""
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question cannot be empty")

    # Get the active rulebook
    document = (
        db.query(RulesDocument)
        .filter(RulesDocument.is_active == True)  # noqa: E712
        .order_by(RulesDocument.uploaded_at.desc())
        .first()
    )
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active rules document available")

    # Download PDF and extract text
    try:
        pdf_bytes = storage_service.get_file_bytes(document.storage_path)
        rulebook_text = ai_service.extract_text_from_pdf(pdf_bytes)
    except Exception as exc:
        logger.error("Failed to load rulebook text: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load the rulebook. Please try again.",
        ) from exc

    # Ask Claude
    try:
        answer = ai_service.ask(question, rulebook_text)
    except Exception as exc:
        logger.error("AI service error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not get an answer right now. Please try again.",
        ) from exc

    # Log the event
    background_tasks.add_task(
        _log_event,
        db,
        str(current_user.id),
        EventType.search,
        {"question": question[:200], "document_id": str(document.id)},
    )

    return AskResponse(answer=answer)


@router.post(
    "/admin/rules/upload",
    response_model=RulesDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_rules(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
    version_label: str = Form(...),
) -> RulesDocument:
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted",
        )

    file_bytes = file.file.read()
    file_size = len(file_bytes)

    # Validate PDF magic bytes (%PDF-)
    if not file_bytes.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File does not appear to be a valid PDF (missing PDF header)",
        )

    # Validate it's a readable PDF
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            page_count = len(pdf.pages)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File does not appear to be a valid PDF: {exc}",
        ) from exc

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    safe_name = f"{timestamp}_{file.filename}"

    try:
        storage_path = storage_service.upload_file(file_bytes, safe_name, content_type="application/pdf")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Storage error: {exc}") from exc

    # Deactivate all previous versions
    db.query(RulesDocument).filter(RulesDocument.is_active == True).update(  # noqa: E712
        {"is_active": False}, synchronize_session=False
    )

    document = RulesDocument(
        filename=file.filename or safe_name,
        storage_path=storage_path,
        version_label=version_label,
        is_active=True,
        file_size_bytes=file_size,
        uploaded_by=current_user.id,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    return document


@router.get("/admin/rules/history", response_model=list[RulesDocumentResponse])
def get_rules_history(
    _current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    skip: int = 0,
    limit: int = 50,
) -> list[RulesDocument]:
    documents = (
        db.query(RulesDocument)
        .order_by(RulesDocument.uploaded_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return documents
