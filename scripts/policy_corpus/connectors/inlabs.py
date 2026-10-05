"""Imprensa Nacional — INLABS daily DOU editions (XML).

INLABS (https://inlabs.in.gov.br) serves the complete DOU as one ZIP per
section per day, free since 2020-01-01, after a free registration. The flow
mirrors the Imprensa Nacional's own ``inlabs-auto`` script:

    POST https://inlabs.in.gov.br/logar.php   form: email, password
         -> session cookie ``inlabs_session_cookie``
    GET  https://inlabs.in.gov.br/index.php?p=YYYY-MM-DD&dl=YYYY-MM-DD-DO1.zip
         -> ZIP of one XML file per article (404 when the edition does not exist,
            e.g. weekends/holidays)

Section codes: DO1 (atos normativos), DO2 (pessoal), DO3 (contratos/editais),
DO1E/DO2E/DO3E (edições extra). For a policy corpus DO1 and DO1E are the
ones that matter; DO3 only if you want licensing notices and auctions.

Article XML (one per file)::

    <xml><article id=".." name="portaria-n-1234-de-..." idOficio=".." pubName="DO1"
                  artType="Portaria" pubDate="23/09/2026" artClass=".."
                  artCategory="Ministério de Minas e Energia/Gabinete do Ministro"
                  numberPage="12" pdfPage="https://pesquisa.in.gov.br/imprensa/jsp/visualiza/index.jsp?..."
                  editionNumber="183" ...>
      <body><Identifica>PORTARIA MME Nº 1.234, DE 22 DE SETEMBRO DE 2026</Identifica>
            <Data>..</Data><Ementa>..</Ementa><Titulo>..</Titulo><SubTitulo/>
            <Texto>&lt;p&gt;...html escaped...&lt;/p&gt;</Texto></body></article></xml>

The XML attribute vocabulary above is the documented INLABS format; the
parser is defensive (missing attributes -> empty strings) because the
Imprensa Nacional has changed attribute sets before.

Credentials: ``INLABS_EMAIL`` / ``INLABS_PASSWORD`` env vars, or the same keys
in the repository ``.env``. Never cached: the login POST is excluded from the
disk cache and the cookie is kept in memory only.
"""

from __future__ import annotations

import datetime as dt
import html
import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Iterator

from ..models import ConnectorResult, Document, Layer, Status, normalize_number
from ..terms import match_terms
from . import Context

LOGIN_URL = "https://inlabs.in.gov.br/logar.php"
DOWNLOAD_URL = "https://inlabs.in.gov.br/index.php"
COOKIE_NAME = "inlabs_session_cookie"
DEFAULT_SECTIONS = ("DO1", "DO1E")

_TAG_RE = re.compile(r"<[^>]+>")
_IDENT_NUM = re.compile(r"N[ºo°.]?\s*([\d.]+)", re.IGNORECASE)
_IDENT_DATE = re.compile(
    r"DE\s+(\d{1,2})[º°]?\s+DE\s+([A-ZÇ]+)\s+DE\s+(\d{4})", re.IGNORECASE
)
_MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}


def credentials() -> tuple[str, str] | None:
    email = os.environ.get("INLABS_EMAIL")
    password = os.environ.get("INLABS_PASSWORD")
    if email and password:
        return email, password
    return None


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    unescaped = html.unescape(text)
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", unescaped)).strip()


def parse_identifica(identifica: str) -> tuple[str, str | None]:
    """``"PORTARIA MME Nº 1.234, DE 22 DE SETEMBRO DE 2026"`` -> ("1234", "2026-09-22")."""
    number = None
    m = _IDENT_NUM.search(identifica or "")
    if m:
        number = normalize_number(m.group(1))
    date = None
    m2 = _IDENT_DATE.search(identifica or "")
    if m2:
        day, month_name, year = int(m2.group(1)), m2.group(2).lower(), int(m2.group(3))
        from ..models import strip_accents
        month = _MONTHS.get(strip_accents(month_name)) or _MONTHS.get(month_name)
        if month:
            try:
                date = dt.date(year, month, day).isoformat()
            except ValueError:
                date = None
    return number or "", date


def parse_article(xml_bytes: bytes) -> dict[str, Any] | None:
    """Parse one INLABS article XML into a flat dict. Returns None when the
    file has no <article> element."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    article = root if root.tag == "article" else root.find(".//article")
    if article is None:
        return None
    body = article.find("body")
    fields = {k: (article.get(k) or "") for k in (
        "id", "name", "idOficio", "pubName", "artType", "pubDate", "artClass",
        "artCategory", "numberPage", "pdfPage", "editionNumber", "idMateria",
    )}
    for child_name in ("Identifica", "Data", "Ementa", "Titulo", "SubTitulo", "Texto"):
        el = body.find(child_name) if body is not None else None
        fields[child_name] = strip_html(el.text if el is not None else "")
    return fields


def iter_articles(zip_bytes: bytes) -> Iterator[tuple[str, dict[str, Any]]]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".xml"):
                continue
            parsed = parse_article(zf.read(name))
            if parsed:
                yield name, parsed


def document_from_article(fields: dict[str, Any], edition_date: dt.date, section: str) -> Document:
    identifica = fields.get("Identifica", "")
    number, act_date = parse_identifica(identifica)
    art_type = fields.get("artType") or "ato"
    ementa = fields.get("Ementa", "")
    texto = fields.get("Texto", "")
    matched, strength = match_terms(identifica, ementa, fields.get("Titulo", ""), texto)
    issuer = (fields.get("artCategory") or "").split("/")[0].strip()
    year = int(act_date[:4]) if act_date else edition_date.year
    name = fields.get("name") or ""
    url = f"https://www.in.gov.br/web/dou/-/{name}" if name else ""
    return Document(
        source="inlabs",
        layer=Layer.FEDERAL_REGULATORY,
        jurisdiction="BR",
        doc_type=art_type.lower(),
        number=number,
        year=year,
        title=identifica or fields.get("Titulo") or name,
        ementa=ementa,
        issuer=issuer,
        house="",
        date=act_date or edition_date.isoformat(),
        url=url,
        text_url=fields.get("pdfPage") or "",
        status=Status.PUBLISHED,
        matched_terms=matched,
        match_strength=strength,
        extra={
            "dou_section": section,
            "dou_edition_date": edition_date.isoformat(),
            "dou_edition_number": fields.get("editionNumber"),
            "dou_page": fields.get("numberPage"),
            "art_category": fields.get("artCategory"),
            "art_class": fields.get("artClass"),
            "inlabs_id": fields.get("id"),
            "text_excerpt": texto[:1500],
        },
    )


def sweep(ctx: Context) -> ConnectorResult:
    res = ConnectorResult(name="inlabs")
    creds = credentials()
    if creds is None:
        res.errors.append("INLABS_EMAIL/INLABS_PASSWORD not set; skipping DOU sweep (register free at https://inlabs.in.gov.br)")
        return res
    sections = tuple(ctx.options.get("dou_sections") or DEFAULT_SECTIONS)
    days = int(ctx.options.get("dou_days") or 30)
    start = max(ctx.since, ctx.until - dt.timedelta(days=days - 1))

    if ctx.fetcher.dry_run:
        for i in range((ctx.until - start).days + 1):
            day = start + dt.timedelta(days=i)
            for section in sections:
                ctx.fetcher.planned.append({"method": "GET", "url": DOWNLOAD_URL, "params": {"p": day.isoformat(), "dl": f"{day.isoformat()}-{section}.zip"}})
        res.notes.append(f"dry-run: {((ctx.until - start).days + 1) * len(sections)} edition downloads planned")
        return res

    cookie: str | None = None
    if not ctx.fetcher.offline:
        login = ctx.fetcher.post(LOGIN_URL, data={"email": creds[0], "password": creds[1]}, allow_cache=False)
        res.requests_made += 1
        cookie = ctx.fetcher.session.cookies.get(COOKIE_NAME)
        if not cookie:
            res.errors.append(f"INLABS login failed (HTTP {login.status}); no {COOKIE_NAME} cookie returned")
            return res

    for i in range((ctx.until - start).days + 1):
        day = start + dt.timedelta(days=i)
        for section in sections:
            params = {"p": day.isoformat(), "dl": f"{day.isoformat()}-{section}.zip"}
            headers = {"Cookie": f"{COOKIE_NAME}={cookie}", "origem": "667285015"} if cookie else None
            try:
                r = ctx.fetcher.get(DOWNLOAD_URL, params=params, headers=headers)
            except Exception as exc:  # OfflineMiss and friends
                res.notes.append(f"{day} {section}: {exc}")
                continue
            res.requests_made += 1
            if r.status == 404:
                continue  # no edition that day (weekend/holiday) — expected
            if not r.ok:
                res.errors.append(f"{day} {section}: HTTP {r.status} {r.error or ''}".strip())
                continue
            if not r.content[:2] == b"PK":
                res.errors.append(f"{day} {section}: response is not a ZIP (session expired or HTML login page)")
                continue
            try:
                for _name, fields in iter_articles(r.content):
                    doc = document_from_article(fields, day, section)
                    if doc.match_strength == "none":
                        continue
                    doc.retrieved_at, doc.http_status = r.retrieved_at, r.status
                    res.documents.append(doc)
            except zipfile.BadZipFile as exc:
                res.errors.append(f"{day} {section}: bad zip ({exc})")
    res.notes.append(f"scanned {start}..{ctx.until} sections={','.join(sections)}; kept {len(res.documents)} articles")
    return res
