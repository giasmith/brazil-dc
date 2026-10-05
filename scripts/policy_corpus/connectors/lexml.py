"""LexML Brasil — SRU (Search/Retrieve via URL) over the federated acervo.

Endpoint (from the Senado "Acervo do portal LexML" open-data page and the
py-lexml-acervo wrapper; a live probe was blocked by robots.txt on 2026-09-23,
so treat the CQL index names as documentation-level):

    GET https://www.lexml.gov.br/busca/SRU
        ?operation=searchRetrieve&version=1.1
        &query=<CQL>&startRecord=1&maximumRecords=100

CQL examples known to work per the wrapper: ``date=2019``,
``urn any decreto and date any 2018``. Fields: ``urn, date, tipoDocumento,
autoridade, localidade`` plus the Dublin Core ones (``dc.title`` ...).

The response is SRW XML; records are Dublin Core with LexML extensions.
Parsing here is namespace-agnostic (local names only) because the acervo mixes
``srw_dc:dc`` and ``dc:`` prefixes across sources.

Why this connector matters: it is the only source that hands you the
``urn:lex`` identifier, and the URN resolver ``https://www.lexml.gov.br/urn/<urn>``
is a stable landing page for the norm regardless of which portal hosts the text.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from ..models import ConnectorResult, Document, Layer, Status, normalize_type
from ..terms import match_terms
from . import Context

SRU = "https://www.lexml.gov.br/busca/SRU"
PAGE = 100

# LexML locality slugs for the priority states (URN component after "br;").
STATE_LOCALITY = {
    "CE": "ceara",
    "SP": "sao.paulo",
    "MG": "minas.gerais",
    "RS": "rio.grande.do.sul",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_sru(xml_text: str) -> tuple[int, list[dict[str, list[str]]], int | None]:
    """Return (numberOfRecords, records, nextRecordPosition).

    Each record is a dict local-name -> list of text values found under
    ``recordData``. Diagnostics (SRU errors) raise ``ValueError`` with the
    message so the caller can log it instead of treating it as "no results".
    """
    if not xml_text or not xml_text.strip():
        return 0, [], None
    root = ET.fromstring(xml_text)
    diagnostics = [el for el in root.iter() if _local(el.tag) == "diagnostic"]
    if diagnostics:
        msgs = []
        for d in diagnostics:
            parts = [c.text.strip() for c in d if c.text and _local(c.tag) in ("message", "details", "uri")]
            msgs.append(" | ".join(parts))
        raise ValueError("SRU diagnostic: " + "; ".join(msgs))
    total = 0
    next_pos: int | None = None
    for el in root.iter():
        name = _local(el.tag)
        if name == "numberOfRecords" and el.text and el.text.strip().isdigit():
            total = int(el.text.strip())
        elif name == "nextRecordPosition" and el.text and el.text.strip().isdigit():
            next_pos = int(el.text.strip())
    records: list[dict[str, list[str]]] = []
    for rec in root.iter():
        if _local(rec.tag) != "recordData":
            continue
        fields: dict[str, list[str]] = {}
        for el in rec.iter():
            if el is rec:
                continue
            text = (el.text or "").strip()
            if text:
                fields.setdefault(_local(el.tag), []).append(text)
        if fields:
            records.append(fields)
    return total, records, next_pos


def _first(fields: dict[str, list[str]], *names: str) -> str:
    for n in names:
        vals = fields.get(n)
        if vals:
            return vals[0]
    return ""


def _urn(fields: dict[str, list[str]]) -> str | None:
    for val in fields.get("identifier", []) + fields.get("urn", []):
        if val.startswith("urn:lex:"):
            return val
    return None


_URN_PARTS = ("jurisdiction", "authority", "type", "when_number")


def split_urn(urn: str) -> dict[str, str]:
    """``urn:lex:br:federal:lei:2026-09-15;15504`` ->
    {jurisdiction: br, locality: '', authority: federal, type: lei, date: 2026-09-15, number: 15504}.
    Tolerates partial URNs (year only) and missing number."""
    out = {"jurisdiction": "", "locality": "", "authority": "", "type": "", "date": "", "number": ""}
    if not urn.startswith("urn:lex:"):
        return out
    parts = urn[len("urn:lex:"):].split(":")
    if len(parts) < 4:
        return out
    juris = parts[0]
    if ";" in juris:
        out["jurisdiction"], out["locality"] = juris.split(";", 1)
    else:
        out["jurisdiction"] = juris
    out["authority"] = parts[1]
    out["type"] = parts[2]
    tail = parts[3]
    if ";" in tail:
        out["date"], out["number"] = tail.split(";", 1)
    else:
        out["date"] = tail
    out["number"] = out["number"].split("@")[0].split("!")[0]  # strip version/fragment
    return out


def document_from_record(fields: dict[str, list[str]], jurisdiction: str = "BR", layer: Layer = Layer.FEDERAL_NORM) -> Document:
    urn = _urn(fields)
    parts = split_urn(urn) if urn else {}
    title = _first(fields, "title")
    description = _first(fields, "description")
    date = parts.get("date") or _first(fields, "date")
    year = int(date[:4]) if date[:4].isdigit() else None
    doc_type = parts.get("type", "").replace(".", " ") or normalize_type(_first(fields, "tipoDocumento", "type"))
    number = parts.get("number") or ""
    matched, strength = match_terms(title, description, " ".join(fields.get("subject", [])))
    locality = parts.get("locality") or ""
    jur = jurisdiction
    if locality:
        uf = {v: k for k, v in STATE_LOCALITY.items()}.get(locality)
        jur = f"BR-{uf}" if uf else f"BR-{locality}"
        layer = Layer.STATE_NORM
    return Document(
        source="lexml",
        layer=layer,
        jurisdiction=jur,
        doc_type=doc_type,
        number=number,
        year=year,
        title=title or (urn or "lexml record"),
        ementa=description,
        issuer=_first(fields, "autoridade", "publisher"),
        house="",
        date=date if len(date) == 10 else None,
        url=f"https://www.lexml.gov.br/urn/{urn}" if urn else "",
        text_url="",
        status=Status.IN_FORCE if layer in (Layer.FEDERAL_NORM, Layer.STATE_NORM) else Status.UNKNOWN,
        urn=urn,
        matched_terms=matched,
        match_strength=strength,
        extra={
            "lexml_tipoDocumento": _first(fields, "tipoDocumento"),
            "lexml_localidade": _first(fields, "localidade"),
            "lexml_autoridade": _first(fields, "autoridade"),
            "lexml_subjects": fields.get("subject", []),
            "lexml_locality": locality,
            "status_note": "LexML does not expose vigência; in_force is a default, not a fact",
        },
    )


def _query(ctx: Context, res: ConnectorResult, cql: str) -> list[dict[str, list[str]]]:
    start = 1
    pages = 0
    out: list[dict[str, list[str]]] = []
    while pages < ctx.max_pages:
        r = ctx.fetcher.get(SRU, params={"operation": "searchRetrieve", "version": "1.1", "query": cql, "startRecord": start, "maximumRecords": PAGE})
        res.requests_made += 1
        pages += 1
        if not r.ok:
            if r.error != "dry_run":
                res.errors.append(f"SRU {cql!r} start={start}: HTTP {r.status} {r.error or ''}".strip())
            break
        try:
            total, records, next_pos = parse_sru(r.text)
        except (ET.ParseError, ValueError) as exc:
            res.errors.append(f"SRU {cql!r} start={start}: {exc}")
            break
        out.extend(records)
        if not next_pos or next_pos > total or not records:
            break
        start = next_pos
    return out


def sweep(ctx: Context) -> ConnectorResult:
    res = ConnectorResult(name="lexml")
    seen: dict[str, dict[str, list[str]]] = {}
    queries = [f'"{t}"' for t in ctx.query_terms]
    # State norms for priority states (documentation-level index name).
    for uf in ctx.states:
        loc = STATE_LOCALITY.get(uf.upper())
        if loc:
            queries.extend(f'"{t}" and localidade any "{loc}"' for t in ctx.query_terms[:2])
    for cql in queries:
        for rec in _query(ctx, res, cql):
            key = _urn(rec) or _first(rec, "title")
            if key and key not in seen:
                seen[key] = rec
    # Exact URN lookups requested by config (e.g. known Redata chain).
    for urn in ctx.config.get("lexml_urn_lookups", []):
        for rec in _query(ctx, res, f'urn = "{urn}"'):
            key = _urn(rec) or _first(rec, "title")
            if key and key not in seen:
                seen[key] = rec
    for rec in seen.values():
        doc = document_from_record(rec)
        if doc.match_strength == "none" and doc.urn not in set(ctx.config.get("lexml_urn_lookups", [])):
            res.notes.append(f"near-miss: {doc.title}")
            continue
        res.documents.append(doc)
    res.notes.append(f"unique records: {len(seen)}; kept: {len(res.documents)}")
    return res
