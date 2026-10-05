"""Gold-set recall check against the curated Phase 3 corpus.

The curated ``docs/phase3_redata_policy_corpus.json`` is the gold set. After a
sweep, every gold document that an automated connector *could* have found
(legal texts, bills, regulatory acts) is looked up in the swept corpus by:

1. ``urn`` equality,
2. (doc_type, number, year) equality after normalisation,
3. URL equality (last resort; Planalto URLs are stable).

Gold rows whose ``type`` is news, a ministry notice, a FUNAI explainer or a
journal article are reported as ``not_discoverable`` and excluded from the
recall denominator — a legislative API cannot be blamed for not indexing a
gov.br press release. The report lists every miss with the key it was looked
up by, which is the list you hand-check when recall drops.
"""

from __future__ import annotations

import re
from typing import Any

from .linking import urn_family as _urn_family
from .models import Document, build_urn, canonical_type, normalize_number

# ``type`` values in docs/phase3_redata_policy_corpus.json that a legislative
# or regulatory API could plausibly index. Everything else (news, ministry
# notices, FUNAI explainers, consultations, journal articles, the Constitution
# without a number) is reported separately and excluded from the denominator.
DISCOVERABLE_TYPES = {
    "federal_law",
    "federal_decree",
    "complementary_law",
    "regulatory_resolution",
    "government_legal_text",
    "legislative_docket",
    "government_regulation",
    "regulatory_act",
    "state_fiscal_agreement",
}

_TITLE_PATTERNS = [
    # Order matters: "Projeto de Lei" and "Lei Complementar" before bare "Lei".
    (re.compile(r"projeto\s+de\s+lei\s*(?:complementar\s*)?(?:n[o.º°]*\s*)?([\d.]+)\s*/\s*(\d{4})", re.I), "pl"),
    (re.compile(r"medida\s+provis[oó]ria\s*(?:n[o.º°]*\s*)?([\d.]+)\s*/\s*(\d{4})", re.I), "medida provisoria"),
    (re.compile(r"lei\s+complementar\s*(?:n[o.º°]*\s*)?([\d.]+)\s*/\s*(\d{4})", re.I), "lei complementar"),
    (re.compile(r"\blei\s*(?:n[o.º°]*\s*)?([\d.]+)\s*/\s*(\d{4})", re.I), "lei"),
    (re.compile(r"decreto\s*(?:n[o.º°]*\s*)?([\d.]+)\s*/\s*(\d{4})", re.I), "decreto"),
    # "Resolucao CONAMA No. 428/2010", "Resolucao Normativa ANEEL No. 1.122/2025", "Resolucao Anatel No. 780/2025"
    (re.compile(r"resolu[cç][aã]o\s+(?:(?:conama|anatel|aneel|normativa|cnpe|cmn|ana)\s+)*(?:n[o.º°]*\s*)?([\d.]+)\s*/\s*(\d{4})", re.I), "resolucao"),
    (re.compile(r"portaria\s+(?:(?:[a-z]{2,6}|normativa|interministerial)\s+)*(?:n[o.º°]*\s*)?([\d.]+)\s*/\s*(\d{4})", re.I), "portaria"),
    (re.compile(r"conv[eê]nio\s+icms\s*(?:n[o.º°]*\s*)?([\d.]+)\s*/\s*(\d{4})", re.I), "convenio icms"),
    (re.compile(r"mo[cç][aã]o\s+(?:conama\s+)?(?:n[o.º°]*\s*)?([\d.]+)\s*/\s*(\d{4})", re.I), "mocao"),
    (re.compile(r"ac[oó]rd[aã]o\s+(?:anatel\s+)?(?:n[o.º°]*\s*)?([\d.]+)\s*/\s*(\d{4})", re.I), "acordao"),
]


def gold_key(gold: dict[str, Any]) -> dict[str, Any]:
    """Derive lookup keys from a gold entry. Uses explicit ``urn``/``doc_type``/
    ``number``/``year`` when the entry has them, else parses the title."""
    out: dict[str, Any] = {"id": gold.get("id"), "urn": gold.get("urn"), "url": gold.get("url"), "doc_type": None, "number": None, "year": None}
    if gold.get("doc_type") and gold.get("number") and gold.get("year"):
        out.update(doc_type=canonical_type(gold["doc_type"]), number=normalize_number(gold["number"]), year=int(gold["year"]))
    else:
        title = gold.get("title") or ""
        for rx, doc_type in _TITLE_PATTERNS:
            m = rx.search(title)
            if m:
                out.update(doc_type=canonical_type(doc_type), number=normalize_number(m.group(1)), year=int(m.group(2)))
                break
    if not out["urn"] and out["doc_type"] and out["number"] and out["year"]:
        out["urn"] = build_urn(out["doc_type"], out["number"], out["year"], gold.get("date"))
    out["discoverable"] = (gold.get("type") in DISCOVERABLE_TYPES) and bool(out["doc_type"])
    return out


def compute_recall(gold_docs: list[dict[str, Any]], corpus: list[Document]) -> dict[str, Any]:
    by_urn = {_urn_family(d.urn): d for d in corpus if d.urn}
    by_tuple: dict[tuple[str, str, int | None], Document] = {}
    for d in corpus:
        # Index under the canonical type so "mpv" (Câmara) and
        # "medida provisoria" (LexML/Planalto) are one act; house is ignored so
        # a bill matches whichever chamber's row was swept first.
        by_tuple.setdefault((canonical_type(d.doc_type), d.number, d.year), d)
    by_url = {d.url.rstrip("/").lower(): d for d in corpus if d.url}

    hits: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    not_discoverable: list[str] = []
    per_category: dict[str, dict[str, int]] = {}
    for gold in gold_docs:
        key = gold_key(gold)
        cat = str(gold.get("category", "uncategorized"))
        if not key["discoverable"]:
            not_discoverable.append(str(gold.get("id")))
            continue
        per_category.setdefault(cat, {"hit": 0, "miss": 0})
        found: Document | None = None
        how = None
        fam = _urn_family(key["urn"])
        if fam and fam in by_urn:
            found, how = by_urn[fam], "urn"
        elif key["doc_type"] and (key["doc_type"], key["number"], key["year"]) in by_tuple:
            found, how = by_tuple[(key["doc_type"], key["number"], key["year"])], "type_number_year"
        elif key["url"] and key["url"].rstrip("/").lower() in by_url:
            found, how = by_url[key["url"].rstrip("/").lower()], "url"
        if found:
            per_category[cat]["hit"] += 1
            hits.append({"gold_id": key["id"], "matched_by": how, "corpus_key": found.key, "source": found.source})
        else:
            per_category[cat]["miss"] += 1
            misses.append({"gold_id": key["id"], "looked_up": {k: key[k] for k in ("urn", "doc_type", "number", "year", "url")}})
    denom = len(hits) + len(misses)
    return {
        "recall": (len(hits) / denom) if denom else None,
        "hits": len(hits),
        "misses": len(misses),
        "not_discoverable": len(not_discoverable),
        "per_category": {c: {**v, "recall": (v["hit"] / (v["hit"] + v["miss"])) if (v["hit"] + v["miss"]) else None} for c, v in per_category.items()},
        "hit_detail": hits,
        "miss_detail": misses,
        "not_discoverable_ids": not_discoverable,
    }
