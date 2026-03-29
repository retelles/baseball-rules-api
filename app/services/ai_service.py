import logging
import io
import re

import pdfplumber
import anthropic

from app.config import settings

logger = logging.getLogger(__name__)


class AIService:
    """Uses Claude to answer questions about the baseball rulebook."""

    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None
        self._pages: list[dict] | None = None  # [{"page": 1, "text": "..."}]
        self._cached_doc_id: str | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not settings.ANTHROPIC_API_KEY:
                raise RuntimeError("ANTHROPIC_API_KEY is not configured")
            self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        return self._client

    def extract_pages_from_pdf(self, pdf_bytes: bytes) -> list[dict]:
        """Extract text from each page of a PDF."""
        pages: list[dict] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                page_text = page.extract_text()
                if page_text and page_text.strip():
                    pages.append({"page": i, "text": page_text})
        return pages

    def get_pages(self, document_id: str, pdf_bytes_loader) -> list[dict]:
        """Get cached pages, or extract and cache them."""
        if self._pages and self._cached_doc_id == document_id:
            return self._pages

        logger.info("Extracting pages from PDF for doc %s", document_id)
        pdf_bytes = pdf_bytes_loader()
        self._pages = self.extract_pages_from_pdf(pdf_bytes)
        self._cached_doc_id = document_id
        logger.info("Cached %d pages", len(self._pages))
        return self._pages

    def find_relevant_pages(self, question: str, pages: list[dict], max_pages: int = 15) -> str:
        """Find the most relevant pages for a question using keyword matching."""
        # Extract keywords from question (words 3+ chars, lowered)
        words = re.findall(r'[a-zA-Z]{3,}', question.lower())
        # Remove common stop words
        stop_words = {
            "the", "and", "for", "are", "but", "not", "you", "all",
            "can", "had", "her", "was", "one", "our", "out", "has",
            "what", "when", "where", "how", "who", "which", "that",
            "this", "with", "from", "they", "been", "have", "many",
            "some", "them", "than", "its", "into", "does", "will",
            "each", "about", "there",
        }
        keywords = [w for w in words if w not in stop_words]

        if not keywords:
            keywords = words[:5]

        # Score each page by keyword matches
        scored = []
        for page in pages:
            text_lower = page["text"].lower()
            score = sum(text_lower.count(kw) for kw in keywords)
            if score > 0:
                scored.append((score, page))

        # Sort by score descending, take top pages
        scored.sort(key=lambda x: x[0], reverse=True)
        top_pages = [p for _, p in scored[:max_pages]]

        # Sort selected pages by page number for coherent reading
        top_pages.sort(key=lambda p: p["page"])

        if not top_pages:
            # Fallback: first 10 pages (likely table of contents + intro rules)
            top_pages = pages[:10]

        parts = [f"--- Page {p['page']} ---\n{p['text']}" for p in top_pages]
        return "\n\n".join(parts)

    def ask(self, question: str, pages: list[dict]) -> str:
        """Find relevant pages and ask Claude about them."""
        context = self.find_relevant_pages(question, pages)

        system_prompt = (
            "You are a helpful Little League baseball rules expert. "
            "You answer questions based ONLY on the rulebook excerpts provided below. "
            "Be concise but thorough. If the excerpts don't cover the question, say so. "
            "When possible, reference the specific rule number or section and page. "
            "Keep answers friendly and easy to understand for coaches, parents, and players.\n\n"
            "RELEVANT RULEBOOK EXCERPTS:\n"
            f"{context}"
        )

        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": question}],
        )

        return response.content[0].text

    def warm_cache(self) -> None:
        """Pre-load the active rulebook pages at startup."""
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
            self._pages = self.extract_pages_from_pdf(pdf_bytes)
            self._cached_doc_id = str(document.id)
            logger.info("Rulebook cache warmed (%d pages)", len(self._pages))
        except Exception as exc:
            logger.error("Failed to warm rulebook cache: %s", exc)
        finally:
            db.close()


ai_service = AIService()
