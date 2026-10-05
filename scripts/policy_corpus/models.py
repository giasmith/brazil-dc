"""Core data model for the SCN Phase 3 policy corpus.

Design rules (see docs/policy_corpus_pipeline.md):

* Every document carries a lifecycle ``status`` drawn from a closed enum. A
  presence-only flag (``has_redata_core_law``) is not enough: MP 1.318/2025 was
  "core law" for 120 days and then lapsed.
* The primary key is the LexML ``urn:lex`` identifier whenever it can be
  built; otherwise a deterministic synthetic key from
  (jurisdiction, house/issuer, type, number, year). The same norm fetched from
  Planalto, LexML, the DOU and the Senado therefore dedupes to one row.
* Relations are first-class rows so that MP -> PL -> Lei -> Decreto is a chain,
  not four unrelated documents.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import re
import unicodedata
from typing import Any


class Status(str, enum.Enum):
    """Lifecycle status. ``UNVERIFIED`` is for rows produced by adapters whose
    endpoint shape could not be verified live; they must be reviewed."""

    PROPOSED = "proposed"
    IN_FORCE = "in_force"
    ENACTED = "enacted"  # a bill that was converted into a norm
    LAPSED = "lapsed"  # e.g. MP that lost validity without conversion
    ARCHIVED = "archived"
    PARTIALLY_VETOED = "partially_vetoed"
    VETO_OVERRIDDEN = "veto_overridden"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    SUB_JUDICE = "sub_judice"
    CONSULTATION_OPEN = "consultation_open"
    CONSULTATION_CLOSED = "consultation_closed"
    PUBLISHED = "published"  # gazettes, news, literature: no lifecycle
    UNKNOWN = "unknown"
    UNVERIFIED = "unverified"


class Layer(str, enum.Enum):
    FEDERAL_BILL = "federal_bill"
    FEDERAL_NORM = "federal_norm"
    FEDERAL_REGULATORY = "federal_regulatory"
    CONSULTATION = "consultation"
    STATE_FISCAL = "state_fiscal"
    STATE_BILL = "state_bill"
    STATE_NORM = "state_norm"
    MUNICIPAL_GAZETTE = "municipal_gazette"
    MUNICIPAL_BILL = "municipal_bill"
    JURISPRUDENCE = "jurisprudence"
    PLANNING = "planning"
    LITERATURE = "literature"
    GOVERNMENT_COMMUNICATION = "government_communication"


class RelationType(str, enum.Enum):
    AMENDS = "amends"
    REVOKES = "revokes"
    REGULATES = "regulates"  # decree/portaria regulating a law
    CONVERTED_FROM = "converted_from"  # Lei <- PL/MP
    ENACTED_AS = "enacted_as"  # PL/MP -> Lei
    REPLACES = "replaces"  # PL 278/2026 replaces lapsed MP 1.318/2025
    SUSPENDED_BY = "suspended_by"
    CHALLENGED_BY = "challenged_by"  # ADI at STF
    PROMULGATED_PARTS_OF = "promulgated_parts_of"  # veto override text
    ADOPTED_BY = "adopted_by"  # CONFAZ convênio adopted by a state
    DERIVED_FROM_CONSULTATION = "derived_from_consultation"
    CITES = "cites"


# LexML document-type slugs for federal norms (subset that matters here).
LEXML_TYPE_SLUGS = {
    "lei": "lei",
    "lei complementar": "lei.complementar",
    "lc": "lei.complementar",
    "lcp": "lei.complementar",
    "decreto": "decreto",
    "medida provisoria": "medida.provisoria",
    "mpv": "medida.provisoria",
    "mp": "medida.provisoria",
    "emenda constitucional": "emenda.constitucional",
    "constituicao": "constituicao",
    "resolucao": "resolucao",
    "portaria": "portaria",
    "instrucao normativa": "instrucao.normativa",
}

_NUMBER_CLEAN = re.compile(r"[.\s]")


def strip_accents(text: str) -> str:
    """Return ``text`` without diacritics (NFKD decomposition, combining marks
    dropped). Keeps case; callers lowercase separately when they need to."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_number(number: str | int | None) -> str:
    """``"1.318"`` -> ``"1318"``, ``"15.504"`` -> ``"15504"``, ``"0278"`` -> ``"278"``.

    Brazilian norm numbers are printed with thousands separators; APIs return
    them without. Leading zeros appear in some state systems. Both must dedupe.
    """
    if number is None:
        return ""
    cleaned = _NUMBER_CLEAN.sub("", str(number)).strip()
    if not cleaned:
        return ""
    return cleaned.lstrip("0") or "0"


def normalize_type(doc_type: str | None) -> str:
    """Lowercase, accent-stripped, whitespace-collapsed document type."""
    if not doc_type:
        return ""
    value = strip_accents(str(doc_type)).lower().strip()
    return re.sub(r"\s+", " ", value)


# House siglas that denote the same act as a LexML norm type. A Medida
# Provisória is one act whether the Câmara lists it as "MPV" or LexML as
# "medida.provisoria"; bills (PL, PLP, PEC) are NOT aliased to the norms they
# may become.
TYPE_ALIASES = {
    "mpv": "medida provisoria",
    "mp": "medida provisoria",
    "lc": "lei complementar",
    "lcp": "lei complementar",
    "ren": "resolucao normativa",
    "resolucao normativa": "resolucao",
    "convenio": "convenio icms",
}


def canonical_type(doc_type: str | None) -> str:
    """``normalize_type`` plus alias folding, applied until stable."""
    value = normalize_type(doc_type)
    seen = set()
    while value in TYPE_ALIASES and value not in seen:
        seen.add(value)
        value = TYPE_ALIASES[value]
    return value


def build_urn(
    doc_type: str,
    number: str | int,
    year: int | str | None = None,
    date: dt.date | str | None = None,
    authority: str = "federal",
    locality: str | None = None,
) -> str | None:
    """Build a LexML-style ``urn:lex`` identifier.

    ``urn:lex:br:federal:lei:2026-09-15;15504`` when the full date is known,
    ``urn:lex:br:federal:lei:2026;15504`` (partial URN, still resolvable by the
    LexML search) when only the year is known. State norms use
    ``urn:lex:br;sao.paulo:estadual:lei:...``. Returns ``None`` for types that
    LexML does not identify this way (bills, gazettes, news).
    """
    slug = LEXML_TYPE_SLUGS.get(normalize_type(doc_type))
    if slug is None:
        return None
    num = normalize_number(number)
    if not num:
        return None
    if isinstance(date, str) and date:
        try:
            date = dt.date.fromisoformat(date[:10])
        except ValueError:
            date = None
    if isinstance(date, dt.date):
        when = date.isoformat()
    elif year:
        when = str(int(year))
    else:
        return None
    jurisdiction = "br" if not locality else f"br;{locality}"
    return f"urn:lex:{jurisdiction}:{authority}:{slug}:{when};{num}"


@dataclasses.dataclass
class Document:
    """One policy document (bill, norm, regulatory act, gazette hit, ...)."""

    source: str  # connector name: camara, senado, lexml, inlabs, ...
    layer: Layer
    jurisdiction: str  # "BR", "BR-CE", "BR-SP-3550308"
    doc_type: str  # pl, lei, decreto, medida provisoria, portaria, convenio icms...
    number: str
    year: int | None
    title: str
    ementa: str = ""
    issuer: str = ""
    house: str = ""  # CD, SF, CN, ALESP, ALMG, ...
    date: str | None = None  # ISO date when known
    url: str = ""
    text_url: str = ""
    status: Status = Status.UNKNOWN
    text_variant: str = "as_published"  # original | compilado | veto_override_promulgation | as_published
    urn: str | None = None
    matched_terms: list[str] = dataclasses.field(default_factory=list)
    match_strength: str = "none"  # high | medium | none
    retrieved_at: str | None = None
    http_status: int | None = None
    sha256_bytes: str | None = None
    sha256_text: str | None = None
    encoding: str | None = None
    verified_adapter: bool = True
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.layer, str) and not isinstance(self.layer, Layer):
            self.layer = Layer(self.layer)
        if isinstance(self.status, str) and not isinstance(self.status, Status):
            self.status = Status(self.status)
        self.number = normalize_number(self.number)
        self.doc_type = normalize_type(self.doc_type)
        if self.year is not None:
            try:
                self.year = int(self.year)
            except (TypeError, ValueError):
                self.year = None
        if self.urn is None:
            authority = "federal"
            locality = None
            if self.jurisdiction.startswith("BR-") and self.layer in (Layer.STATE_NORM,):
                authority = "estadual"
                locality = self.extra.get("lexml_locality")
            if self.layer in (Layer.FEDERAL_NORM, Layer.STATE_NORM):
                self.urn = build_urn(
                    self.doc_type, self.number, self.year, self.date, authority, locality
                )

    @property
    def key(self) -> str:
        """Dedupe key: URN when present, else synthetic."""
        if self.urn:
            return self.urn
        who = self.house or strip_accents(self.issuer).lower().replace(" ", ".")[:24] or "na"
        year = self.year if self.year is not None else "na"
        return f"{self.jurisdiction.lower()}:{who.lower()}:{self.doc_type}:{self.number or 'na'}:{year}"

    def to_record(self) -> dict[str, Any]:
        rec = dataclasses.asdict(self)
        rec["layer"] = self.layer.value
        rec["status"] = self.status.value
        rec["key"] = self.key
        return rec


@dataclasses.dataclass
class Relation:
    src: str  # Document.key
    dst: str  # Document.key
    type: RelationType
    evidence: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.type, str) and not isinstance(self.type, RelationType):
            self.type = RelationType(self.type)

    def to_record(self) -> dict[str, Any]:
        return {
            "src": self.src,
            "dst": self.dst,
            "type": self.type.value,
            "evidence": self.evidence,
            "source": self.source,
        }


@dataclasses.dataclass
class ConnectorResult:
    name: str
    documents: list[Document] = dataclasses.field(default_factory=list)
    relations: list[Relation] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)
    errors: list[str] = dataclasses.field(default_factory=list)
    requests_made: int = 0
    verified_adapter: bool = True

    def summary(self) -> dict[str, Any]:
        return {
            "connector": self.name,
            "documents": len(self.documents),
            "relations": len(self.relations),
            "requests": self.requests_made,
            "verified_adapter": self.verified_adapter,
            "notes": self.notes,
            "errors": self.errors,
        }


_IDENT_RE = re.compile(
    r"^\s*([A-Za-z]{2,5})\s*(?:n[ºo°.]?\s*)?(\d[\d.]*)\s*/\s*(\d{4})\s*$"
)


def parse_identificacao(text: str) -> tuple[str, str, int] | None:
    """``"PL 278/2026"`` -> ``("PL", "278", 2026)``; ``"MPV 1.318/2025"`` ->
    ``("MPV", "1318", 2025)``. Returns ``None`` when the pattern is absent."""
    if not text:
        return None
    m = _IDENT_RE.match(text)
    if not m:
        return None
    sigla, number, year = m.group(1).upper(), normalize_number(m.group(2)), int(m.group(3))
    return sigla, number, year


def dedupe_documents(documents: list[Document]) -> tuple[list[Document], int]:
    """Merge documents sharing a key. Keeps the first row but unions matched
    terms and fills empty fields from later rows. Returns (unique, merged_count)."""
    by_key: dict[str, Document] = {}
    merged = 0
    for doc in documents:
        k = doc.key
        if k not in by_key:
            by_key[k] = doc
            continue
        merged += 1
        base = by_key[k]
        for term in doc.matched_terms:
            if term not in base.matched_terms:
                base.matched_terms.append(term)
        if base.match_strength == "none" and doc.match_strength != "none":
            base.match_strength = doc.match_strength
        for field_name in ("ementa", "title", "url", "text_url", "date", "issuer", "urn"):
            if not getattr(base, field_name) and getattr(doc, field_name):
                setattr(base, field_name, getattr(doc, field_name))
        if base.status == Status.UNKNOWN and doc.status != Status.UNKNOWN:
            base.status = doc.status
        sources = set(str(base.extra.get("also_seen_in", "")).split("|")) - {""}
        sources.add(doc.source)
        base.extra["also_seen_in"] = "|".join(sorted(sources))
    return list(by_key.values()), merged
