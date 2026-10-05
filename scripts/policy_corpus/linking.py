"""Cross-source relation inference.

Connectors only know their own rows. This step looks across the merged corpus
and adds the relations that make the lifecycle chain explicit:

* Câmara row and Senado row for the same bill (same sigla/number/year) ->
  ``CITES`` both ways with evidence "same bill, two houses" so the graph
  shows one bill, not two.
* A bill whose status is ``ENACTED`` and whose Senado ``tipoNorma`` lookup
  produced an ``ENACTED_AS`` relation gets the mirror ``CONVERTED_FROM``.
* An MPV (Câmara/Senado) with status ``LAPSED`` and a PL that names the same
  regime in its ementa (configurable pairs) -> ``REPLACES``.
* Curated relations from the Phase 3 JSON (``relations`` per document) are
  imported verbatim so hand-verified links survive automated runs.
"""

from __future__ import annotations

import re
from typing import Any

from .models import Document, Relation, RelationType, Status, build_urn, normalize_number, normalize_type


def link_same_bill_across_houses(docs: list[Document]) -> list[Relation]:
    rels: list[Relation] = []
    by_bill: dict[tuple[str, str, int | None], list[Document]] = {}
    for d in docs:
        if d.layer.value == "federal_bill" and d.doc_type and d.number:
            by_bill.setdefault((d.doc_type, d.number, d.year), []).append(d)
    for (_t, _n, _y), rows in by_bill.items():
        houses = {r.house for r in rows}
        if len(rows) < 2 or len(houses) < 2:
            continue
        rows = sorted(rows, key=lambda r: r.house)
        for a, b in zip(rows, rows[1:]):
            rels.append(Relation(a.key, b.key, RelationType.CITES, evidence="same bill tracked by two houses", source="linking"))
    return rels


def mirror_enacted(relations: list[Relation]) -> list[Relation]:
    out: list[Relation] = []
    existing = {(r.src, r.dst, r.type) for r in relations}
    for r in relations:
        if r.type == RelationType.ENACTED_AS and (r.dst, r.src, RelationType.CONVERTED_FROM) not in existing:
            out.append(Relation(r.dst, r.src, RelationType.CONVERTED_FROM, evidence=f"mirror of {r.evidence}", source="linking"))
    return out


def curated_relations(gold_docs: list[dict[str, Any]]) -> list[Relation]:
    """Import ``relations`` arrays from the curated corpus JSON. Targets that
    are document ids are mapped to a synthetic key ``gold:<ID>`` unless the
    entry carries a urn; consumers join on either."""
    rels: list[Relation] = []
    gold_key_by_id = {str(g.get("id")): (g.get("urn") or f"gold:{g.get('id')}") for g in gold_docs}
    for g in gold_docs:
        src = gold_key_by_id.get(str(g.get("id")), f"gold:{g.get('id')}")
        for rel in g.get("relations") or []:
            if not isinstance(rel, dict) or not rel.get("type") or not rel.get("target"):
                continue
            target = str(rel["target"])
            dst = target if target.startswith("urn:lex:") else gold_key_by_id.get(target, f"gold:{target}")
            try:
                rels.append(Relation(src, dst, RelationType(rel["type"]), evidence=str(rel.get("evidence", "curated")), source="curated"))
            except ValueError:
                continue
    return rels


def replaces_pairs(docs: list[Document], pairs: list[dict[str, Any]]) -> list[Relation]:
    """``pairs``: [{"lapsed": {"doc_type": "mpv", "number": "1318", "year": 2025},
    "replacement": {"doc_type": "pl", "number": "278", "year": 2026}}]."""
    rels: list[Relation] = []
    index: dict[tuple[str, str, int], list[Document]] = {}
    for d in docs:
        if d.year is not None:
            index.setdefault((d.doc_type, d.number, d.year), []).append(d)
    for pair in pairs:
        try:
            a = pair["lapsed"]
            b = pair["replacement"]
            ka = (normalize_type(a["doc_type"]), normalize_number(a["number"]), int(a["year"]))
            kb = (normalize_type(b["doc_type"]), normalize_number(b["number"]), int(b["year"]))
        except (KeyError, TypeError, ValueError):
            continue
        # The lapsed act may be indexed under its house sigla ("mpv") or its
        # LexML type ("medida provisoria"); accept either spelling.
        lapsed_rows = index.get(ka, []) + (index.get(("mpv",) + ka[1:], []) if ka[0] == "medida provisoria" else [])
        if ka[0] == "mpv":
            lapsed_rows += index.get(("medida provisoria",) + ka[1:], [])
        replacements = index.get(kb, [])
        if lapsed_rows:
            for da in lapsed_rows:
                for db in replacements:
                    rels.append(Relation(db.key, da.key, RelationType.REPLACES, evidence="configured lapsed->replacement pair", source="linking"))
            continue
        # Lapsed act not in the swept set: point at its URN so the chain is
        # still explicit (LexML/Planalto rows dedupe onto that URN later).
        urn = build_urn(a.get("doc_type", ""), a.get("number", ""), a.get("year"), a.get("date"))
        if urn:
            for db in replacements:
                rels.append(Relation(db.key, urn, RelationType.REPLACES, evidence="configured pair; lapsed act referenced by urn", source="linking"))
    return rels


_URN_FAMILY = re.compile(r"^(urn:lex:[^:]+:[^:]+:[^:]+:)(\d{4})(?:-\d{2}-\d{2})?;(.+)$")


def urn_family(urn: str | None) -> str | None:
    """Year-only form of a URN so full-date and partial URNs compare equal."""
    if not urn:
        return None
    m = _URN_FAMILY.match(urn)
    return f"{m.group(1)}{m.group(2)};{m.group(3)}" if m else urn


def canonicalize_relations(relations: list[Relation], docs: list[Document], aliases: dict[str, str] | None = None) -> list[Relation]:
    """Rewrite relation endpoints onto swept document keys where possible:

    * any endpoint present in ``aliases`` (``gold:<ID>`` or a gold URN that
      the recall check matched) -> that corpus key, so curated links attach to
      real rows even when the swept row has no URN (bills, MPs as tracked by a
      house);
    * a partial (year-only) URN -> the full-date URN of the single swept
      document in that URN family.

    Endpoints that cannot be resolved are left as they are (they still carry
    meaning: a URN is resolvable at lexml.gov.br/urn/<urn>). Self-loops and
    duplicates produced by the rewrite are dropped, order preserved.
    """
    aliases = aliases or {}
    family_to_key: dict[str, list[str]] = {}
    for d in docs:
        fam = urn_family(d.urn)
        if fam:
            family_to_key.setdefault(fam, []).append(d.key)

    def resolve(endpoint: str) -> str:
        if endpoint in aliases:
            return aliases[endpoint]
        if endpoint.startswith("urn:lex:"):
            keys = family_to_key.get(urn_family(endpoint) or "", [])
            if len(keys) == 1:
                return keys[0]
        return endpoint

    out: list[Relation] = []
    seen: set[tuple[str, str, str]] = set()
    for r in relations:
        src, dst = resolve(r.src), resolve(r.dst)
        if src == dst:
            continue
        k = (src, dst, r.type.value)
        if k in seen:
            continue
        seen.add(k)
        out.append(Relation(src, dst, r.type, r.evidence, r.source))
    return out


def status_audit(docs: list[Document]) -> list[str]:
    """Human-readable warnings about lifecycle inconsistencies."""
    notes: list[str] = []
    mps = [d for d in docs if d.doc_type in ("mpv", "medida provisoria")]
    for d in mps:
        if d.status in (Status.PROPOSED, Status.UNKNOWN):
            notes.append(f"{d.title}: MP with status {d.status.value}; MPs lapse after 120 days — confirm conversion or set lapsed")
    return notes
