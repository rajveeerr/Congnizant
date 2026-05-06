"""Mock PII redactor (regex-based).

Phase 11 swaps this for AWS Comprehend's detect_pii_entities. Same
interface: takes text, returns redacted text + list of detected entities.
"""

import re

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")
PHONE_RE = re.compile(
    r"\b(?:\+?\d{1,3}[\s-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}\b"
)
# Crude name heuristic: two consecutive capitalized words, each ≥3 letters.
NAME_RE = re.compile(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b")


def redact(text: str) -> tuple[str, list[dict]]:
    """Returns (redacted_text, [{type, match}, ...])."""
    entities: list[dict] = []

    def _capture(match: re.Match, kind: str) -> str:
        entities.append({"type": kind, "match": match.group(0)})
        return "[REDACTED]"

    text = EMAIL_RE.sub(lambda m: _capture(m, "EMAIL"), text)
    text = PHONE_RE.sub(lambda m: _capture(m, "PHONE"), text)
    text = NAME_RE.sub(lambda m: _capture(m, "NAME"), text)

    return text, entities
