"""Câmara dos Deputados — Dados Abertos API v2.

Verified live on 2026-09-23::

    GET https://dadosabertos.camara.leg.br/api/v2/proposicoes
        ?keywords=data%20center&dataApresentacaoInicio=2025-01-01
        &itens=100&ordem=ASC&ordenarPor=id
    -> {"dados": [{id, uri, siglaTipo, codTipo, numero, ano, ementa,
                   dataApresentacao}], "links": [{rel, href}]}

Edge cases handled here:

* ``keywords`` matches the indexation vocabulary, not full text, so every
  phrase in ``QUERY_TERMS`` is a separate request and ids are unioned.
* Pagination is link-based (``rel == "next"``); the API also caps ``itens`` at
  100. ``max_pages`` bounds runaway loops.
* The detail call (``/proposicoes/{id}``) is what carries
  ``urlInteiroTeor`` (full-text PDF) and ``statusProposicao``; the list call
  does not. ``ctx.detail=False`` skips it for a cheap sweep.
* ``statusProposicao.descricaoSituacao`` values seen in the wild are mapped to
  the ``Status`` enum; anything unmapped stays ``PROPOSED`` and is recorded in
  ``extra["situacao"]`` so the mapping can be extended.
* A ``MPV`` proposição in the Câmara is the same act as the Planalto MP; the
  synthetic key uses house ``CD`` so it does not collide with the Senado row,
  and the relation layer links them by (type, number, year).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..models import ConnectorResult, Document, Layer, Status
from ..terms import match_terms
from . import Context

BASE = "https://dadosabertos.camara.leg.br/api/v2"
ITEMS_PER_PAGE = 100

SITUACAO_TO_STATUS = {
    "transformado em norma juridica": Status.ENACTED,
    "transformada em norma juridica": Status.ENACTED,
    "arquivada": Status.ARCHIVED,
    "arquivado": Status.ARCHIVED,
    "retirado pelo autor": Status.ARCHIVED,
    "retirada pelo autor": Status.ARCHIVED,
    "perda de eficacia": Status.LAPSED,
    "perdeu a eficacia": Status.LAPSED,
    "vetado totalmente": Status.REVOKED,
    "vetada totalmente": Status.REVOKED,
}


def _status_from_situacao(situacao: str | None) -> Status:
    from ..models import normalize_type  # accent/case normalization

    if not situacao:
        return Status.PROPOSED
    norm = normalize_type(situacao)
    for needle, status in SITUACAO_TO_STATUS.items():
        if needle in norm:
            return status
    return Status.PROPOSED


def parse_list_page(payload: dict[str, Any] | None) -> tuple[list[dict[str, Any]], str | None]:
    """Return (items, next_href) from a ``/proposicoes`` list response."""
    if not payload:
        return [], None
    items = payload.get("dados") or []
    next_href = None
    for link in payload.get("links") or []:
        if link.get("rel") == "next" and link.get("href"):
            next_href = link["href"]
            break
    return items, next_href


def document_from_item(item: dict[str, Any], detail: dict[str, Any] | None = None) -> Document:
    """Build a Document from a list item, enriched by the optional detail
    payload (``/proposicoes/{id}`` -> ``dados``)."""
    detail = detail or {}
    sigla = (item.get("siglaTipo") or detail.get("siglaTipo") or "").strip()
    numero = item.get("numero") if item.get("numero") is not None else detail.get("numero")
    ano = item.get("ano") if item.get("ano") is not None else detail.get("ano")
    ementa = item.get("ementa") or detail.get("ementa") or ""
    keywords = detail.get("keywords") or ""
    ementa_detalhada = detail.get("ementaDetalhada") or ""
    status_block = detail.get("statusProposicao") or {}
    situacao = status_block.get("descricaoSituacao")
    matched, strength = match_terms(ementa, ementa_detalhada, keywords)
    date = (item.get("dataApresentacao") or detail.get("dataApresentacao") or "")[:10] or None
    prop_id = item.get("id") or detail.get("id")
    return Document(
        source="camara",
        layer=Layer.FEDERAL_BILL,
        jurisdiction="BR",
        doc_type=sigla.lower(),
        number=str(numero) if numero is not None else "",
        year=ano,
        title=f"{sigla} {numero}/{ano}" if sigla and numero and ano else str(prop_id),
        ementa=ementa,
        issuer="Câmara dos Deputados",
        house="CD",
        date=date,
        url=f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={prop_id}" if prop_id else "",
        text_url=detail.get("urlInteiroTeor") or "",
        status=_status_from_situacao(situacao),
        matched_terms=matched,
        match_strength=strength,
        extra={
            "camara_id": prop_id,
            "situacao": situacao,
            "keywords": keywords,
            "tramitacao_orgao": status_block.get("siglaOrgao"),
            "uri_prop_principal": detail.get("uriPropPrincipal"),
            "cod_tipo": item.get("codTipo") or detail.get("codTipo"),
        },
    )


def request_shapes(term: str, since: dt.date, until: dt.date) -> list[tuple[str, dict[str, Any], bool]]:
    """Request shapes to try in order until the API accepts one.

    The only shape verified live (2026-09-23) was ``keywords + dataApresentacaoInicio
    + itens + ordem + ordenarPor``; the first live run on 2026-09-24 got HTTP 400
    for the fuller shape with ``dataApresentacaoFim`` over a 2.7-year window. Each
    entry is (name, params, needs_client_date_filter). The API's own error text
    is logged for every rejected shape so the rule can be pinned down.
    """
    base = {"itens": ITEMS_PER_PAGE, "ordem": "ASC", "ordenarPor": "id"}
    return [
        ("full_window", {"keywords": term, "dataApresentacaoInicio": since.isoformat(), "dataApresentacaoFim": until.isoformat(), **base}, False),
        ("start_only", {"keywords": term, "dataApresentacaoInicio": since.isoformat(), **base}, True),
        ("no_dates", {"keywords": term, **base}, True),
        ("keywords_only", {"keywords": term}, True),
    ]


def _year_windows(since: dt.date, until: dt.date) -> list[tuple[dt.date, dt.date]]:
    out = []
    for year in range(since.year, until.year + 1):
        out.append((max(since, dt.date(year, 1, 1)), min(until, dt.date(year, 12, 31))))
    return out


def _paginate(ctx: Context, res: ConnectorResult, term: str, params: dict[str, Any]) -> tuple[list[dict[str, Any]], int, str]:
    """Fetch all pages for one request shape. Returns (items, first_status, first_error_detail)."""
    url: str | None = f"{BASE}/proposicoes"
    pages = 0
    items_out: list[dict[str, Any]] = []
    first_status = 0
    first_detail = ""
    while url and pages < ctx.max_pages:
        r = ctx.fetcher.get(url, params=params if pages == 0 else None, headers={"Accept": "application/json"})
        res.requests_made += 1
        pages += 1
        if pages == 1:
            first_status = r.status
            first_detail = r.error_detail() if not r.ok else ""
        if not r.ok:
            if pages > 1 and r.error != "dry_run":
                res.errors.append(f"{term!r} page {pages}: {r.error_detail()}")
            break
        try:
            payload = r.json()
        except ValueError as exc:
            res.errors.append(f"{term!r} page {pages}: invalid JSON ({exc})")
            break
        items, url = parse_list_page(payload)
        items_out.extend(items)
    if pages >= ctx.max_pages and url:
        res.notes.append(f"{term!r}: stopped at max_pages={ctx.max_pages}; results truncated")
    return items_out, first_status, first_detail


def _in_window(item: dict[str, Any], since: dt.date, until: dt.date) -> bool:
    raw = (item.get("dataApresentacao") or "")[:10]
    try:
        when = dt.date.fromisoformat(raw)
    except ValueError:
        return True  # keep undated rows for the client-side term rule to judge
    return since <= when <= until


def sweep(ctx: Context) -> ConnectorResult:
    res = ConnectorResult(name="camara")
    seen: dict[int, dict[str, Any]] = {}
    for term in ctx.query_terms:
        accepted: str | None = None
        rejected: list[str] = []
        for name, params, client_filter in request_shapes(term, ctx.since, ctx.until):
            items, status, detail = _paginate(ctx, res, term, params)
            if status == 0 and ctx.fetcher.dry_run:
                accepted = name
                break
            if status == 400:
                rejected.append(f"{name}: {detail}")
                # A long window may be the reason: retry the same shape per year once.
                if name == "full_window" and ctx.until.year > ctx.since.year:
                    windows = _year_windows(ctx.since, ctx.until)
                    ok_years = 0
                    for start, end in windows:
                        yp = dict(params, dataApresentacaoInicio=start.isoformat(), dataApresentacaoFim=end.isoformat())
                        y_items, y_status, y_detail = _paginate(ctx, res, term, yp)
                        if y_status != 200:
                            rejected.append(f"full_window[{start.year}]: {y_detail}")
                            break
                        ok_years += 1
                        items.extend(y_items)
                    if ok_years == len(windows):
                        accepted = "full_window_per_year"
                        break
                continue
            if status != 200:
                res.errors.append(f"{term!r} ({name}): {detail or f'HTTP {status}'}")
                break
            accepted = name
            if client_filter:
                items = [it for it in items if _in_window(it, ctx.since, ctx.until)]
            break
        if accepted is None:
            res.errors.append(f"{term!r}: every request shape rejected — " + " | ".join(rejected))
            continue
        if rejected:
            res.notes.append(f"{term!r}: shape '{accepted}' accepted after rejections: " + " | ".join(rejected))
        for it in items:
            pid = it.get("id")
            if isinstance(pid, int) and pid not in seen:
                seen[pid] = it

    for pid, item in seen.items():
        detail: dict[str, Any] | None = None
        retrieved_at: str | None = None
        http_status: int | None = None
        if ctx.detail:
            r = ctx.fetcher.get(f"{BASE}/proposicoes/{pid}", headers={"Accept": "application/json"})
            res.requests_made += 1
            retrieved_at, http_status = r.retrieved_at, r.status
            if r.ok:
                try:
                    detail = (r.json() or {}).get("dados") or None
                except ValueError:
                    res.errors.append(f"detail {pid}: invalid JSON")
            elif r.error != "dry_run":
                res.errors.append(f"detail {pid}: {r.error_detail()}")
        doc = document_from_item(item, detail)
        doc.retrieved_at = retrieved_at
        doc.http_status = http_status
        if doc.match_strength == "none":
            res.notes.append(f"near-miss (API matched, client rule did not): {doc.title}")
            continue
        res.documents.append(doc)
    res.notes.append(f"unique proposições from API: {len(seen)}; kept after term rule: {len(res.documents)}")
    return res
