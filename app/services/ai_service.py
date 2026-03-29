import logging
import io

import pdfplumber
import anthropic

from app.config import settings

logger = logging.getLogger(__name__)


class AIService:
    """Uses Claude to answer questions about the baseball rulebook."""

    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None
        self._rulebook_text: str | None = None
        self._cached_doc_id: str | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not settings.ANTHROPIC_API_KEY:
                raise RuntimeError("ANTHROPIC_API_KEY is not configured")
            self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        return self._client

    def extract_text_from_pdf(self, pdf_bytes: bytes) -> str:
        """Extract all text from a PDF."""
        text_parts: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"--- Page {i} ---\n{page_text}")
        return "\n\n".join(text_parts)

    def get_rulebook_text(self, document_id: str, pdf_bytes_loader) -> str:
        """Get cached rulebook text, or extract and cache it."""
        if self._rulebook_text and self._cached_doc_id == document_id:
            logger.info("Using cached rulebook text for doc %s", document_id)
            return self._rulebook_text

        logger.info("Extracting text from PDF for doc %s", document_id)
        pdf_bytes = pdf_bytes_loader()
        self._rulebook_text = self.extract_text_from_pdf(pdf_bytes)
        self._cached_doc_id = document_id
        return self._rulebook_text

    def warm_cache(self) -> None:
        """Pre-load the active rulebook text at startup."""
        from app.database import SessionLocal
        from app.models.rules_document import RulesDocument
        from app.services.storage_service import storage_service

        db = SessionLocal()
        try:
            document = (
                db.query(RulesDocument)
                .filter(RulesDocument.is_active == True)  # noqa: E712
                .order_by(RulesDocument.uploaded_at.desc())
                .first()
            )
            if not document:
                logger.info("No active rulebook to warm cache")
                return

            logger.info("Warming rulebook cache for doc %s", document.id)
            pdf_bytes = storage_service.get_file_bytes(document.storage_path)
            self._rulebook_text = self.extract_text_from_pdf(pdf_bytes)
            self._cached_doc_id = str(document.id)
            logger.info("Rulebook cache warmed (%d chars)", len(self._rulebook_text))
        except Exception as exc:
            logger.error("Failed to warm rulebook cache: %s", exc)
        finally:
            db.close()

    def ask(self, question: str, rulebook_text: str) -> str:
        """Send a question about the rulebook to Claude and return the answer."""
        system_prompt = (
            "You are a helpful Little League baseball rules expert. "
            "You answer questions based ONLY on the official rulebook text provided below. "
            "Be concise but thorough. If the rulebook doesn't cover the question, say so. "
            "When possible, reference the specific rule number or section. "
            "Keep answers friendly and easy to understand for coaches, parents, and players.\n\n"
            "RULEBOOK TEXT:\n"
            f"{rulebook_text}"
        )

        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": question}],
        )

        return response.content[0].text


ai_service = AIService()
