"""Search-term set for data-center policy in Brazilian Portuguese.

Two uses:

* ``QUERY_TERMS`` are sent to APIs that only accept a phrase (Câmara
  ``keywords=``, Senado ``termo=``, LexML CQL, Querido Diário ``querystring``).
  Câmara's ``keywords`` is NOT a full-text OR, so each phrase is a separate
  request and results are unioned by the caller.
* ``match_terms`` is applied client-side to ementa/title/text so that every
  connector applies the same acceptance rule regardless of what its API
  matched on.

Acceptance rule: at least one HIGH-precision term, or two distinct MEDIUM
terms, or one MEDIUM plus one LOW. LOW terms alone never match ("grandes
cargas" and "consumidor livre" are generic electricity-market vocabulary).
"""

from __future__ import annotations

import dataclasses
import re

from .models import strip_accents


@dataclasses.dataclass(frozen=True)
class Term:
    name: str
    pattern: str
    precision: str  # high | medium | low
    case_sensitive: bool = False  # matched on the raw text (acronyms)

    def regex(self) -> re.Pattern[str]:
        flags = 0 if self.case_sensitive else re.IGNORECASE
        return re.compile(self.pattern, flags)


TERMS: tuple[Term, ...] = (
    Term("data center", r"\bdata[\s\-]?cent(er|re)s?\b", "high"),
    Term("centro de dados", r"\bcentros?\s+de\s+dados\b", "high"),
    Term(
        "centro de processamento de dados",
        r"\bcentros?\s+de\s+processamento\s+de\s+dados\b",
        "high",
    ),
    Term("redata", r"\bredata\b", "high"),
    Term("servicos de datacenter", r"\bservicos?\s+de\s+data[\s\-]?center", "high"),
    Term("hiperescala", r"\bhiperescala\b|\bhyperscale\b", "medium"),
    Term("computacao em nuvem", r"\bcomputacao\s+em\s+nuvem\b|\bcloud\s+computing\b", "medium"),
    Term("eficiencia hidrica", r"\beficiencia\s+hidrica\b", "medium"),
    Term("infraestrutura digital", r"\binfraestrutura\s+digital\b", "medium"),
    Term("WUE/PUE", r"\b(?:WUE|PUE)\b", "medium", case_sensitive=True),
    Term("CPD", r"\bCPD\b", "low", case_sensitive=True),
    Term("grandes cargas", r"\bgrandes?\s+cargas?\b", "low"),
    Term("consumidor livre", r"\bconsumidor(?:es)?\s+livres?\b", "low"),
    Term("resfriamento", r"\bresfriamento\b", "low"),
)

QUERY_TERMS: tuple[str, ...] = (
    "data center",
    "datacenter",
    "centro de dados",
    "centro de processamento de dados",
    "redata",
)

_COMPILED = [(t, t.regex()) for t in TERMS]
_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Accent-stripped, lowercased, whitespace-collapsed copy used for the
    case-insensitive patterns. Raw text is kept for case-sensitive acronyms."""
    if not text:
        return ""
    return _WS.sub(" ", strip_accents(text)).lower()


def match_terms(*chunks: str | None) -> tuple[list[str], str]:
    """Return (matched term names, strength) for the concatenated chunks.

    ``strength`` is ``"high"`` when the acceptance rule is met via a HIGH term,
    ``"medium"`` when met via the MEDIUM/LOW combination rule, ``"none"``
    otherwise. Callers should keep ``"none"`` rows out of the corpus but may
    log them as near-misses.
    """
    raw = " ".join(c for c in chunks if c)
    if not raw.strip():
        return [], "none"
    norm = normalize_text(raw)
    matched: list[str] = []
    high = medium = low = 0
    for term, rx in _COMPILED:
        haystack = raw if term.case_sensitive else norm
        if rx.search(haystack):
            matched.append(term.name)
            if term.precision == "high":
                high += 1
            elif term.precision == "medium":
                medium += 1
            else:
                low += 1
    if high:
        return matched, "high"
    if medium >= 2 or (medium >= 1 and low >= 1):
        return matched, "medium"
    return matched, "none"
