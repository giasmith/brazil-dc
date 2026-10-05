"""Querido Diário (Open Knowledge Brasil) — municipal official gazettes.

Public API (official docs, https://docs.queridodiario.ok.org.br):

    GET https://api.queridodiario.ok.org.br/gazettes
        ?querystring="data center"&territory_ids=2304400&territory_ids=3550308
        &published_since=2025-01-01&published_until=2026-09-23
        &size=100&offset=0&excerpt_size=500&number_of_excerpts=3
        &sort_by=descending_date
    -> {"total_gazettes": N, "gazettes": [{territory_id, date, scraped_at, url,
        territory_name, state_code, excerpts: [...], edition, is_extra_edition,
        txt_url}]}

No auth; the project asks for <= 60 requests/minute (the fetcher throttles
this host to 1 s). ``querystring`` supports quoted phrases; ``territory_ids``
is repeated per IBGE code (7 digits).

Coverage caveat: only municipalities with a working scraper are indexed and
OCR quality varies, so this is a recall booster for zoning/local incentive
acts around candidate H3 cells, not ground truth. Each hit is one gazette
edition (not one act): the reviewer reads ``excerpts`` and, if relevant,
fetches ``txt_url`` for the full text.
"""

from __future__ import annotations

from typing import Any

from ..models import ConnectorResult, Document, Layer, Status
from ..terms import match_terms
from . import Context

BASE = "https://api.queridodiario.ok.org.br"
PAGE = 100


def parse_response(payload: dict[str, Any] | None) -> tuple[int, list[dict[str, Any]]]:
    if not payload:
        return 0, []
    total = int(payload.get("total_gazettes") or 0)
    return total, list(payload.get("gazettes") or [])


def document_from_gazette(g: dict[str, Any], query: str) -> Document:
    excerpts = [e for e in (g.get("excerpts") or []) if isinstance(e, str)]
    matched, strength = match_terms(*excerpts)
    if strength == "none" and excerpts:
        # The API matched but the excerpt window may have cut the phrase; keep
        # the row as medium so it reaches review rather than vanishing.
        matched, strength = [f"api:{query}"], "medium"
    territory = str(g.get("territory_id") or "")
    state = (g.get("state_code") or "").upper()
    date = (g.get("date") or "")[:10] or None
    return Document(
        source="querido_diario",
        layer=Layer.MUNICIPAL_GAZETTE,
        jurisdiction=f"BR-{state}-{territory}" if state and territory else "BR-municipal",
        doc_type="diario oficial municipal",
        number=str(g.get("edition") or ""),
        year=int(date[:4]) if date and date[:4].isdigit() else None,
        title=f"{g.get('territory_name', territory)} ({state}) — edição {g.get('edition') or '?'} de {date or '?'}",
        ementa=" … ".join(excerpts)[:1000],
        issuer=str(g.get("territory_name") or territory),
        house="",
        date=date,
        url=g.get("url") or "",
        text_url=g.get("txt_url") or "",
        status=Status.PUBLISHED,
        matched_terms=matched,
        match_strength=strength,
        extra={
            "territory_id": territory,
            "state_code": state,
            "is_extra_edition": g.get("is_extra_edition"),
            "scraped_at": g.get("scraped_at"),
            "excerpts": excerpts,
            "query": query,
        },
    )


def sweep(ctx: Context) -> ConnectorResult:
    res = ConnectorResult(name="querido_diario")
    codes: list[str] = []
    for uf in ctx.states:
        codes.extend(str(c) for c in ctx.config.get("municipalities", {}).get(uf.upper(), []))
    codes.extend(str(c) for c in ctx.options.get("municipalities") or [])
    codes = sorted(set(c for c in codes if c))
    bad = [c for c in codes if not (c.isdigit() and len(c) == 7)]
    if bad:
        res.errors.append(f"IBGE codes must be 7 digits; ignoring {bad}")
        codes = [c for c in codes if c not in bad]
    if not codes:
        res.errors.append("no municipalities configured for states " + ",".join(ctx.states))
        return res
    seen: dict[str, dict[str, Any]] = {}
    for term in ctx.query_terms[:3]:  # gazettes: keep request volume modest
        offset = 0
        pages = 0
        query = f'"{term}"'
        while pages < ctx.max_pages:
            params: dict[str, Any] = {
                "querystring": query,
                "territory_ids": codes,
                "published_since": ctx.since.isoformat(),
                "published_until": ctx.until.isoformat(),
                "size": PAGE,
                "offset": offset,
                "excerpt_size": 500,
                "number_of_excerpts": 3,
                "sort_by": "descending_date",
            }
            r = ctx.fetcher.get(f"{BASE}/gazettes", params=params)
            res.requests_made += 1
            pages += 1
            if not r.ok:
                if r.error != "dry_run":
                    res.errors.append(f"{query} offset={offset}: HTTP {r.status} {r.error or ''}".strip())
                break
            try:
                total, gazettes = parse_response(r.json())
            except ValueError as exc:
                res.errors.append(f"{query} offset={offset}: invalid JSON ({exc})")
                break
            for g in gazettes:
                key = f"{g.get('territory_id')}|{g.get('date')}|{g.get('edition')}|{g.get('is_extra_edition')}"
                if key not in seen:
                    seen[key] = {"gazette": g, "query": query}
            offset += PAGE
            if not gazettes or offset >= total:
                break
    for entry in seen.values():
        doc = document_from_gazette(entry["gazette"], entry["query"])
        res.documents.append(doc)
    res.notes.append(f"{len(codes)} municipalities; {len(seen)} gazette editions matched")
    return res
