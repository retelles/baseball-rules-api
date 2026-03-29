import logging
import io
import re

from pypdf import PdfReader
import anthropic

from app.config import settings

logger = logging.getLogger(__name__)


class AIService:
    """Uses Claude to answer questions about the baseball rulebook."""

    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not settings.ANTHROPIC_API_KEY:
                raise RuntimeError("ANTHROPIC_API_KEY is not configured")
            self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        return self._client

    def extract_text_from_pdf(self, pdf_bytes: bytes) -> str:
        """Extract all text from a PDF (used at upload time)."""
        pages: list[str] = []
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text()
            if text and text.strip():
                pages.append(f"--- Page {i} ---\n{text.strip()}")
        return "\n\n".join(pages)

    def find_relevant_sections(self, question: str, full_text: str, max_chars: int = 25000) -> str:
        """Find the most relevant sections of the rulebook for a question."""
        # Split into pages
        pages = full_text.split("\n\n--- Page ")
        if not pages:
            return full_text[:max_chars]

        # First element doesn't have the split prefix removed
        parsed = []
        for i, chunk in enumerate(pages):
            if i == 0 and chunk.startswith("--- Page "):
                chunk = chunk[len("--- Page "):]
            parsed.append(chunk)

        # Extract keywords from question
        words = re.findall(r'[a-zA-Z]{3,}', question.lower())
        stop_words = {
            "the", "and", "for", "are", "but", "not", "you", "all",
            "can", "had", "her", "was", "one", "our", "out", "has",
            "what", "when", "where", "how", "who", "which", "that",
            "this", "with", "from", "they", "been", "have", "many",
            "some", "them", "than", "its", "into", "does", "will",
            "each", "about", "there", "little", "league",
        }
        keywords = [w for w in words if w not in stop_words]
        if not keywords:
            keywords = words[:5]

        # Score each page
        scored = []
        for chunk in parsed:
            text_lower = chunk.lower()
            score = sum(text_lower.count(kw) for kw in keywords)
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Build context within char limit
        selected = []
        total_chars = 0
        for _, chunk in scored[:10]:
            truncated = chunk[:3000]
            if total_chars + len(truncated) > max_chars:
                break
            selected.append(truncated)
            total_chars += len(truncated)

        if not selected:
            # Fallback: first chunk
            return full_text[:max_chars]

        return "\n\n".join(selected)

    def ask(self, question: str, full_text: str) -> str:
        """Find relevant sections and ask Claude about them."""
        context = self.find_relevant_sections(question, full_text)

        system_prompt = (
            "You are a Little League baseball rules expert. "
            "Answer ONLY from the rulebook excerpts below. "
            "Be SHORT and direct — 2-4 sentences max unless the question requires a list. "
            "Cite the rule number if available. No headers, no markdown formatting, no fluff.\n\n"
            "RULEBOOK EXCERPTS:\n"
            f"{context}"
        )

        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": question}],
        )

        return response.content[0].text


ai_service = AIService()
