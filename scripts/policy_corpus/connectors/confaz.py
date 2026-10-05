"""CONFAZ — Convênios ICMS (state fiscal layer).

Lei 15.504/2026 is federal; the ICMS reduction that actually moves siting
decisions is granted by a CONFAZ convênio that each state chooses to adopt
(press coverage names Convênio ICMS 38/2026 as the data-center one). The
listing page for a year is a Plone folder::

    https://www.confaz.fazenda.gov.br/legislacao/convenios/<year>
    links like /legislacao/convenios/2026/CV038_26

This is an HTML scrape of a site whose markup is not versioned, hence
``verification = unverified``: rows come out with ``status=unverified`` and the
clause text is stored for review, not parsed into fields. State adhesion is
reported as ``extra["states_mentioned"]`` (UF codes found in the text) — a
hint for the reviewer, not a legal fact, because convênios name states in
several ways (authorising clause vs. later "adesão" convênios).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from ..models import ConnectorResult, Document, Layer, Status, normalize_number
from ..terms import match_terms
from . import Context

BASE = "https://www.confaz.fazenda.gov.br"
LISTING = BASE + "/legislacao/convenios/{year}"
_LINK_RE = re.compile(r"/legislacao/convenios/(\d{4})/CV(\d{3})_(\d{2})", re.IGNORECASE)
_TITLE_RE = re.compile(r"CONV[ÊE]NIO\s+ICMS\s+N[ºo°.]?\s*([\d.]+)\s*,?\s*DE\s+(\d{1,2})[º°]?\s+DE\s+([A-ZÇ]+)\s+DE\s+(\d{4})", re.IGNORECASE)
UFS = ("AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO")
_UF_RE = re.compile(r"\b(" + "|".join(UFS) + r")\b")
STATE_NAMES = {
    "acre": "AC", "alagoas": "AL", "amazonas": "AM", "amapa": "AP", "bahia": "BA", "ceara": "CE",
    "distrito federal": "DF", "espirito santo": "ES", "goias": "GO", "maranhao": "MA", "minas gerais": "MG",
    "mato grosso do sul": "MS", "mato grosso": "MT", "para": "PA", "paraiba": "PB", "pernambuco": "PE",
    "piaui": "PI", "parana": "PR", "rio de janeiro": "RJ", "rio grande do norte": "RN", "rondonia": "RO",
    "roraima": "RR", "rio grande do sul": "RS", "santa catarina": "SC", "sergipe": "SE", "sao paulo": "SP",
    "tocantins": "TO",
}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self._chunks: list[str] = []
        self._skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.links.append(v)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3"):
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
        self._chunks.append(data)

    @property
    def text(self) -> str:
        raw = "".join(self._chunks)
        return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", raw)).strip()


def parse_listing(html_text: str) -> list[tuple[str, int, int]]:
    """Return unique (path, year, number) tuples for convênio links."""
    p = _Extractor()
    p.feed(html_text or "")
    out: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for href in p.links:
        m = _LINK_RE.search(href)
        if not m:
            continue
        path = m.group(0)
        if path in seen:
            continue
        seen.add(path)
        out.append((path, int(m.group(1)), int(m.group(2))))
    return out


def parse_convenio(html_text: str) -> dict[str, Any]:
    p = _Extractor()
    p.feed(html_text or "")
    text = p.text
    title = p.title.strip()
    m = _TITLE_RE.search(text[:3000]) or _TITLE_RE.search(title)
    number = normalize_number(m.group(1)) if m else ""
    from ..models import strip_accents

    lowered = strip_accents(text).lower()
    states = set(_UF_RE.findall(text))
    for name, uf in STATE_NAMES.items():
        if re.search(r"\b" + re.escape(name) + r"\b", lowered):
            states.add(uf)
    return {"title": (m.group(0) if m else title), "number": number, "text": text, "states": sorted(states)}


def sweep(ctx: Context) -> ConnectorResult:
    res = ConnectorResult(name="confaz", verified_adapter=False)
    years = sorted({ctx.since.year, ctx.until.year} | set(range(ctx.since.year, ctx.until.year + 1)))
    for year in years:
        r = ctx.fetcher.get(LISTING.format(year=year))
        res.requests_made += 1
        if not r.ok:
            if r.error != "dry_run":
                res.errors.append(f"listing {year}: HTTP {r.status} {r.error or ''}".strip())
            continue
        entries = parse_listing(r.text)
        if not entries:
            res.notes.append(f"listing {year}: no CVnnn_yy links found (markup may have changed)")
        for path, yr, num in entries:
            page = ctx.fetcher.get(BASE + path)
            res.requests_made += 1
            if not page.ok:
                if page.error != "dry_run":
                    res.errors.append(f"{path}: HTTP {page.status} {page.error or ''}".strip())
                continue
            parsed = parse_convenio(page.text)
            matched, strength = match_terms(parsed["title"], parsed["text"][:20000])
            if strength == "none":
                continue
            res.documents.append(Document(
                source="confaz",
                layer=Layer.STATE_FISCAL,
                jurisdiction="BR",
                doc_type="convenio icms",
                number=parsed["number"] or str(num),
                year=yr,
                title=parsed["title"] or f"Convênio ICMS {num}/{yr}",
                ementa=parsed["text"][:600],
                issuer="CONFAZ",
                house="CONFAZ",
                date=None,
                url=BASE + path,
                status=Status.UNVERIFIED,
                matched_terms=matched,
                match_strength=strength,
                retrieved_at=page.retrieved_at,
                http_status=page.status,
                verified_adapter=False,
                extra={"states_mentioned": parsed["states"], "text_excerpt": parsed["text"][:4000]},
            ))
    return res
