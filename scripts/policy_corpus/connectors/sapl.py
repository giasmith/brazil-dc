"""Generic Interlegis SAPL adapter (state assemblies and câmaras municipais).

SAPL (Sistema de Apoio ao Processo Legislativo) is the Senado/Interlegis
system used by hundreds of legislative houses; every instance exposes a
Django-REST-framework API at ``<host>/api/`` with the endpoint
``/api/materia/materialegislativa/`` (documented in interlegis/sapl issue
#2728). DRF conventions:

* pagination: ``{"count", "next", "previous", "results"}`` with ``page=`` /
  ``page_size=``;
* field lookups when the instance enables django-filter, e.g.
  ``?ementa__icontains=data%20center&ano=2026``.

Because filter support differs by SAPL version, the adapter tries the server
filter first and falls back to a year-scoped scan filtered client-side when
the server answers 400 or ignores the filter (detected when the first page
contains rows that do not match). Hosts follow the convention
``https://sapl.<municipio>.<uf>.leg.br``; pass them with ``--sapl-host`` or
in ``sources.json`` under ``sapl_hosts`` (each with ``jurisdiction``).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ..models import ConnectorResult, Document, Layer, Status
from ..terms import match_terms
from . import Context

PAGE_SIZE = 100


def parse_page(payload: dict[str, Any] | list[Any] | None) -> tuple[list[dict[str, Any]], str | None, int]:
    if payload is None:
        return [], None, 0
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)], None, len(payload)
    results = [x for x in (payload.get("results") or []) if isinstance(x, dict)]
    return results, payload.get("next"), int(payload.get("count") or len(results))


def document_from_materia(m: dict[str, Any], host: str, jurisdiction: str) -> Document:
    ementa = m.get("ementa") or ""
    matched, strength = match_terms(ementa, m.get("indexacao") or "", m.get("observacao") or "")
    tipo = m.get("tipo")
    tipo_sigla = ""
    if isinstance(tipo, dict):
        tipo_sigla = tipo.get("sigla") or tipo.get("descricao") or ""
    elif tipo is not None:
        tipo_sigla = str(m.get("tipo_sigla") or m.get("tipo__sigla") or tipo)
    numero = str(m.get("numero") or "")
    ano = m.get("ano")
    mid = m.get("id")
    netloc = urlsplit(host).netloc or host
    return Document(
        source="sapl",
        layer=Layer.MUNICIPAL_BILL if jurisdiction.count("-") >= 2 else Layer.STATE_BILL,
        jurisdiction=jurisdiction,
        doc_type=str(tipo_sigla).lower() or "materia",
        number=numero,
        year=ano,
        title=f"{tipo_sigla} {numero}/{ano} ({netloc})".strip(),
        ementa=ementa,
        issuer=netloc,
        house=netloc,
        date=(m.get("data_apresentacao") or "")[:10] or None,
        url=f"{host.rstrip('/')}/materia/{mid}" if mid else host,
        text_url=str(m.get("texto_original") or ""),
        status=Status.PROPOSED if m.get("em_tramitacao", True) else Status.ARCHIVED,
        matched_terms=matched,
        match_strength=strength,
        extra={"sapl_id": mid, "sapl_host": host, "regime_tramitacao": m.get("regime_tramitacao")},
    )


def _fetch_pages(ctx: Context, res: ConnectorResult, url: str, params: dict[str, Any] | None) -> tuple[list[dict[str, Any]], int | None]:
    rows: list[dict[str, Any]] = []
    pages = 0
    status: int | None = None
    next_url: str | None = url
    first = True
    while next_url and pages < ctx.max_pages:
        r = ctx.fetcher.get(next_url, params=params if first else None, headers={"Accept": "application/json"})
        res.requests_made += 1
        pages += 1
        first = False
        status = r.status
        if not r.ok:
            if r.error != "dry_run":
                res.errors.append(f"{next_url}: HTTP {r.status} {r.error or ''}".strip())
            break
        try:
            results, next_url, _count = parse_page(r.json())
        except ValueError as exc:
            res.errors.append(f"{url}: invalid JSON ({exc})")
            break
        rows.extend(results)
        if not results:
            break
    return rows, status


def sweep(ctx: Context) -> ConnectorResult:
    res = ConnectorResult(name="sapl")
    hosts: list[dict[str, str]] = list(ctx.config.get("sapl_hosts", []))
    for h in ctx.options.get("sapl_hosts") or []:
        hosts.append({"host": h, "jurisdiction": "BR-sapl"})
    if not hosts:
        res.notes.append("no SAPL hosts configured (use --sapl-host https://sapl.<municipio>.<uf>.leg.br)")
        return res
    for entry in hosts:
        host = entry.get("host", "").rstrip("/")
        jurisdiction = entry.get("jurisdiction", "BR-sapl")
        if not host.startswith("http"):
            res.errors.append(f"bad sapl host {host!r}")
            continue
        endpoint = f"{host}/api/materia/materialegislativa/"
        collected: dict[Any, dict[str, Any]] = {}
        server_filter_ok = True
        for term in ctx.query_terms[:3]:
            rows, status = _fetch_pages(ctx, res, endpoint, {"ementa__icontains": term, "page_size": PAGE_SIZE})
            if status == 400:
                server_filter_ok = False
                break
            # Detect ignored filter: rows whose ementa lacks the term.
            if rows and sum(1 for r in rows if term.lower() not in (r.get("ementa") or "").lower()) > len(rows) // 2:
                server_filter_ok = False
                res.notes.append(f"{host}: server ignored ementa__icontains; falling back to year scan")
                break
            for r in rows:
                collected.setdefault(r.get("id"), r)
        if not server_filter_ok:
            for year in range(ctx.since.year, ctx.until.year + 1):
                rows, _ = _fetch_pages(ctx, res, endpoint, {"ano": year, "page_size": PAGE_SIZE})
                for r in rows:
                    collected.setdefault(r.get("id"), r)
        for m in collected.values():
            doc = document_from_materia(m, host, jurisdiction)
            if doc.match_strength == "none":
                continue
            if doc.year is not None and not (ctx.since.year <= doc.year <= ctx.until.year):
                continue
            res.documents.append(doc)
        res.notes.append(f"{host}: {len(collected)} matérias scanned, {sum(1 for d in res.documents if d.extra.get('sapl_host') == host)} kept")
    return res
