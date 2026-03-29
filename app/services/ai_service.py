import logging
import io
import re
import time

from pypdf import PdfReader
import anthropic

from app.config import settings

logger = logging.getLogger(__name__)


class AIService:
    """Uses Claude to answer questions about the baseball rulebook."""

    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None
        self._pages: list[dict] | None = None
        self._cached_doc_id: str | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not settings.ANTHROPIC_API_KEY:
                raise RuntimeError("ANTHROPIC_API_KEY is not configured")
            self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        return self._client

    def extract_pages_from_pdf(self, pdf_bytes: bytes) -> list[dict]:
        """Extract text from each page using pypdf (fast)."""
        t0 = time.time()
        pages: list[dict] = []
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text()
            if text and text.strip():
                pages.append({"page": i, "text": text.strip()})
        logger.info("Extracted %d pages in %.1fs", len(pages), time.time() - t0)
        return pages

    def get_pages(self, document_id: str, pdf_bytes_loader) -> list[dict]:
        """Get cached pages, or extract and cache them."""
        if self._pages and self._cached_doc_id == document_id:
            return self._pages

        logger.info("Extracting pages from PDF for doc %s", document_id)
        pdf_bytes = pdf_bytes_loader()
        self._pages = self.extract_pages_from_pdf(pdf_bytes)
        self._cached_doc_id = document_id
        return self._pages

    def find_relevant_pages(self, question: str, pages: list[dict], max_pages: int = 10) -> str:
        """Find the most relevant pages for a question using keyword matching."""
        words = re.findall(r'[a-zA-Z]{3,}', question.lower())
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

        scored = []
        for page in pages:
            text_lower = page["text"].lower()
            score = sum(text_lower.count(kw) for kw in keywords)
            if score > 0:
                scored.append((score, page))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_pages = [p for _, p in scored[:max_pages]]
        top_pages.sort(key=lambda p: p["page"])

        if not top_pages:
            top_pages = pages[:5]

        # Truncate each page to keep context manageable
        parts = []
        for p in top_pages:
            text = p["text"][:3000]
            parts.append(f"--- Page {p['page']} ---\n{text}")
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

        t0 = time.time()
        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": question}],
        )
        logger.info("Claude API call took %.1fs", time.time() - t0)
        return response.content[0].text

    def warm_cache(self) -> None:
        """Pre-load the active rulebook pages at startup."""
        from app.database import SessionLocal
        from app.models.rules_document import RulesDocument
        from app.services.storage_service import storage_service

        logger.info("warm_cache starting...")
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

            t0 = time.time()
            pdf_bytes = storage_service.get_file_bytes(document.storage_path)
            logger.info("R2 download took %.1fs (%d bytes)", time.time() - t0, len(pdf_bytes))

            self._pages = self.extract_pages_from_pdf(pdf_bytes)
            self._cached_doc_id = str(document.id)
            logger.info("Cache warmed: %d pages, doc %s", len(self._pages), document.id)
        except Exception as exc:
            logger.error("warm_cache FAILED: %s", exc, exc_info=True)
        finally:
            db.close()


ai_service = AIService()
