"""State-assembly adapters for the priority states (CE, SP, MG, RS).

Each state exposes something different, which is exactly why this layer is
where a corpus goes stale:

* **SP — ALESP**: bulk XML, refreshed daily (~03:30). Path confirmed from a
  third-party monitor on 2026-09-23::

      https://www.al.sp.gov.br/repositorioDados/processo_legislativo/proposituras.zip

  The record schema is not published, so ``parse_alesp_records`` maps fields
  by case-insensitive tag names (``*ementa*``, ``*ano*``, ``*nro*``/``*numero*``,
  ``*natureza*``, ``iddocumento``). ALESP also publishes ``legislacao_normas``
  (state laws) on the same portal; add its ZIP path to ``sources.json`` once
  confirmed. The portal announces a move to ``ckan.al.sp.gov.br``.
* **MG — ALMG**: has a real open-data API with Swagger
  (``https://dadosabertos.almg.gov.br/api/ajuda/swagger/view/lastest``) which
  could not be read from here. The adapter targets the legacy
  ``/ws/proposicoes/pesquisa/direcionada?expr=&formato=json`` shape and is
  marked unverified.
* **RS — AL-RS**: an undocumented internal JSON API used by the assembly's
  own site (``POST https://ww4.al.rs.gov.br:5000/listaProposicaoCompleto``).
  Unverified; expect occasional instability (the fetcher retries).
* **CE — ALECE**: the ``/api/`` at www2.al.ce.gov.br is procurement-only.
  Bills need an HTML scraper of the legislative search; this adapter is a
  stub that says so and points at the LexML state filter and Querido Diário
  (Fortaleza / Caucaia / São Gonçalo do Amarante for Pecém) as substitutes.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Iterator

from ..models import ConnectorResult, Document, Layer, Status
from ..terms import match_terms
from . import Context

ALESP_PROPOSITURAS = "https://www.al.sp.gov.br/repositorioDados/processo_legislativo/proposituras.zip"
ALMG_WS = "https://dadosabertos.almg.gov.br/ws/proposicoes/pesquisa/direcionada"
ALRS_API = "https://ww4.al.rs.gov.br:5000/listaProposicaoCompleto"


# ----------------------------------------------------------------------- ALESP
def _pick(rec: dict[str, str], *needles: str) -> str:
    for needle in needles:
        for k, v in rec.items():
            if needle in k.lower() and v:
                return v
    return ""


def iter_alesp_records(xml_stream: io.BufferedReader | io.BytesIO) -> Iterator[dict[str, str]]:
    """Stream records from proposituras.xml without loading it whole.

    A record is any depth-1 element under the root; its direct children become
    tag -> text. Unknown schema is tolerated by design.
    """
    depth = 0
    current: dict[str, str] | None = None
    for event, el in ET.iterparse(xml_stream, events=("start", "end")):
        if event == "start":
            depth += 1
            if depth == 2:
                current = {}
            continue
        # end
        if depth == 3 and current is not None:
            current[el.tag.rsplit("}", 1)[-1]] = (el.text or "").strip()
        elif depth == 2 and current is not None:
            yield current
            current = None
            el.clear()
        depth -= 1


def document_from_alesp(rec: dict[str, str]) -> Document:
    ementa = _pick(rec, "ementa")
    matched, strength = match_terms(ementa)
    ano = _pick(rec, "anolegislativo", "ano")
    numero = _pick(rec, "nrolegislativo", "numero", "nro")
    natureza = _pick(rec, "naturezasigla", "siglanatureza", "natureza")
    doc_id = _pick(rec, "iddocumento", "id")
    date = _pick(rec, "dtentradasistema", "dtpublicacao", "data")[:10]
    year = int(ano) if ano.isdigit() else None
    return Document(
        source="alesp",
        layer=Layer.STATE_BILL,
        jurisdiction="BR-SP",
        doc_type=(natureza or "propositura").lower(),
        number=numero,
        year=year,
        title=f"{natureza or 'Propositura'} {numero}/{ano} (ALESP)".strip(),
        ementa=ementa,
        issuer="ALESP",
        house="ALESP",
        date=date if len(date) == 10 else None,
        url=f"https://www.al.sp.gov.br/propositura/?id={doc_id}" if doc_id else "https://www.al.sp.gov.br/",
        status=Status.PROPOSED,
        matched_terms=matched,
        match_strength=strength,
        extra={"alesp_id": doc_id, "raw_keys": sorted(rec.keys())[:40]},
    )


def sweep_alesp(ctx: Context) -> ConnectorResult:
    res = ConnectorResult(name="alesp")
    if "SP" not in [s.upper() for s in ctx.states]:
        res.notes.append("SP not in --states; skipped")
        return res
    r = ctx.fetcher.get(ALESP_PROPOSITURAS)
    res.requests_made += 1
    if not r.ok:
        if r.error != "dry_run":
            res.errors.append(f"proposituras.zip: HTTP {r.status} {r.error or ''}".strip())
        return res
    if r.content[:2] != b"PK":
        res.errors.append("proposituras.zip: not a ZIP (portal may have moved to ckan.al.sp.gov.br)")
        return res
    scanned = 0
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            members = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            if not members:
                res.errors.append("proposituras.zip: no XML member")
                return res
            with zf.open(members[0]) as stream:
                for rec in iter_alesp_records(stream):
                    scanned += 1
                    doc = document_from_alesp(rec)
                    if doc.match_strength == "none":
                        continue
                    if doc.year is not None and not (ctx.since.year <= doc.year <= ctx.until.year):
                        continue
                    doc.retrieved_at, doc.http_status = r.retrieved_at, r.status
                    res.documents.append(doc)
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        res.errors.append(f"proposituras.zip: {exc}")
    res.notes.append(f"scanned {scanned} proposituras; kept {len(res.documents)}")
    return res


# ------------------------------------------------------------------------ ALMG
def _find_list(payload: Any, must_have: tuple[str, ...] = ("ementa",)) -> list[dict[str, Any]]:
    """Depth-first search for the first list of dicts carrying ``must_have`` keys."""
    if isinstance(payload, list):
        if payload and all(isinstance(x, dict) for x in payload) and any(
            k in payload[0] or k.capitalize() in payload[0] for k in must_have
        ):
            return payload
        for item in payload:
            found = _find_list(item, must_have)
            if found:
                return found
    elif isinstance(payload, dict):
        for v in payload.values():
            found = _find_list(v, must_have)
            if found:
                return found
    return []


def _generic_document(source: str, house: str, jurisdiction: str, item: dict[str, Any], url_base: str) -> Document:
    def g(*names: str) -> Any:
        for n in names:
            for k, v in item.items():
                if k.lower() == n.lower() and v not in (None, ""):
                    return v
        return ""

    ementa = str(g("ementa", "assunto", "descricao"))
    matched, strength = match_terms(ementa, str(g("indexacao", "palavrasChave", "resumo")))
    ano = g("ano", "anoProposicao", "anoLegislativo")
    numero = str(g("numero", "num", "numeroProposicao"))
    tipo = g("siglaTipo", "tipo", "sigla", "tipoProposicao")
    if isinstance(tipo, dict):
        tipo = tipo.get("sigla") or tipo.get("descricao") or ""
    ident = g("id", "idProposicao", "codigo", "seq")
    try:
        year = int(str(ano)[:4]) if str(ano)[:4].isdigit() else None
    except ValueError:
        year = None
    return Document(
        source=source,
        layer=Layer.STATE_BILL,
        jurisdiction=jurisdiction,
        doc_type=str(tipo).lower() or "proposicao",
        number=numero,
        year=year,
        title=f"{tipo} {numero}/{ano} ({house})".strip(),
        ementa=ementa,
        issuer=house,
        house=house,
        date=str(g("dataApresentacao", "data", "dataPublicacao"))[:10] or None,
        url=f"{url_base}{ident}" if ident else url_base,
        status=Status.UNVERIFIED,
        matched_terms=matched,
        match_strength=strength,
        verified_adapter=False,
        extra={"raw_keys": sorted(item.keys())[:40], "raw_id": ident},
    )


def sweep_almg(ctx: Context) -> ConnectorResult:
    res = ConnectorResult(name="almg", verified_adapter=False)
    if "MG" not in [s.upper() for s in ctx.states]:
        res.notes.append("MG not in --states; skipped")
        return res
    seen: dict[Any, dict[str, Any]] = {}
    for term in ctx.query_terms[:3]:
        r = ctx.fetcher.get(ALMG_WS, params={"expr": term, "formato": "json"}, headers={"Accept": "application/json"})
        res.requests_made += 1
        if not r.ok:
            if r.error != "dry_run":
                res.errors.append(f"expr={term!r}: HTTP {r.status} {r.error or ''}".strip())
            continue
        try:
            payload = r.json()
        except ValueError:
            res.errors.append(f"expr={term!r}: non-JSON response (endpoint shape probably differs; check ALMG swagger)")
            continue
        for item in _find_list(payload):
            key = item.get("id") or item.get("idProposicao") or (item.get("numero"), item.get("ano"), item.get("siglaTipo"))
            seen.setdefault(key, item)
    for item in seen.values():
        doc = _generic_document("almg", "ALMG", "BR-MG", item, "https://www.almg.gov.br/atividade-parlamentar/projetos-de-lei/texto/?id=")
        if doc.match_strength == "none":
            continue
        res.documents.append(doc)
    res.notes.append(f"{len(seen)} items; kept {len(res.documents)} (UNVERIFIED adapter)")
    return res


def sweep_alrs(ctx: Context) -> ConnectorResult:
    res = ConnectorResult(name="alrs", verified_adapter=False)
    if "RS" not in [s.upper() for s in ctx.states]:
        res.notes.append("RS not in --states; skipped")
        return res
    seen: dict[Any, dict[str, Any]] = {}
    for year in range(ctx.since.year, ctx.until.year + 1):
        r = ctx.fetcher.post(ALRS_API, json_body={"ano": year}, headers={"Accept": "application/json"})
        res.requests_made += 1
        if not r.ok:
            if r.error != "dry_run":
                res.errors.append(f"ano={year}: HTTP {r.status} {r.error or ''}".strip())
            continue
        try:
            payload = r.json()
        except ValueError:
            res.errors.append(f"ano={year}: non-JSON response (internal API changed?)")
            continue
        for item in _find_list(payload, ("ementa", "assunto", "descricao")):
            key = item.get("id") or item.get("idProposicao") or item.get("seq") or (item.get("numero"), item.get("ano"), item.get("tipo"))
            seen.setdefault(key, item)
    for item in seen.values():
        doc = _generic_document("alrs", "AL-RS", "BR-RS", item, "https://www.al.rs.gov.br/legislativo/Proposicoes.aspx?id=")
        if doc.match_strength == "none":
            continue
        res.documents.append(doc)
    res.notes.append(f"{len(seen)} items; kept {len(res.documents)} (UNVERIFIED adapter)")
    return res


def sweep_alece(ctx: Context) -> ConnectorResult:
    res = ConnectorResult(name="alece", verified_adapter=False)
    if "CE" not in [s.upper() for s in ctx.states]:
        res.notes.append("CE not in --states; skipped")
        return res
    res.notes.append(
        "ALECE exposes no bills API (www2.al.ce.gov.br/api/ is licitações/contratos only). "
        "Covered instead by: lexml (localidade=ceara), querido_diario (Fortaleza 2304400, "
        "Caucaia 2303709, São Gonçalo do Amarante 2312403 — Pecém) and any --sapl-host for "
        "câmaras municipais. A scraper of https://www2.al.ce.gov.br/legislativo/ is the manual path."
    )
    return res
