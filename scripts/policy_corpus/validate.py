"""Schema/consistency validation for the curated Phase 3 corpus JSON.

Catches the failure modes that a presence-only flag lets through:

* a ``redata_core_law`` category with no ``in_force`` document (the MP lapsed
  and nobody added the Lei);
* relations pointing at ids that do not exist;
* dates that are not ISO (``"updated"``, ``"2025-10"``) — warnings, since
  gov.br pages legitimately lack a day;
* duplicate ids / urls; http instead of https; unknown status values.

Exit code is 1 on any error so this can gate the Phase 3 script in CI.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from .models import RelationType, Status

_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_]+$")


def _is_iso_date(value: str) -> bool:
    try:
        dt.date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def validate_corpus(path: Path) -> tuple[list[str], list[str], dict[str, Any]]:
    """Return (errors, warnings, summary)."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read {path}: {exc}"], [], {}
    if not isinstance(data, dict):
        return ["top-level JSON must be an object"], [], {}
    for key in ("corpus_name", "last_updated", "documents"):
        if key not in data:
            errors.append(f"missing top-level key {key!r}")
    if "last_updated" in data and not _is_iso_date(str(data["last_updated"])):
        errors.append(f"last_updated is not an ISO date: {data['last_updated']!r}")
    docs = data.get("documents") or []
    if not isinstance(docs, list):
        return errors + ["documents must be a list"], warnings, {}

    ids: dict[str, int] = {}
    urls: dict[str, list[str]] = {}
    statuses = {s.value for s in Status}
    rel_types = {r.value for r in RelationType}
    by_category: dict[str, list[dict[str, Any]]] = {}
    for i, doc in enumerate(docs):
        where = f"documents[{i}]"
        if not isinstance(doc, dict):
            errors.append(f"{where}: not an object")
            continue
        doc_id = str(doc.get("id", ""))
        where = f"{where} ({doc_id or 'no id'})"
        if not doc_id:
            errors.append(f"{where}: missing id")
        elif not _ID_RE.match(doc_id):
            warnings.append(f"{where}: id is not UPPER_SNAKE")
        if doc_id in ids:
            errors.append(f"{where}: duplicate id (also documents[{ids[doc_id]}])")
        ids.setdefault(doc_id, i)
        for req in ("category", "type", "title", "url", "phase3_use"):
            if not doc.get(req):
                errors.append(f"{where}: missing {req}")
        url = str(doc.get("url", ""))
        if url.startswith("http://"):
            warnings.append(f"{where}: http url (prefer https): {url}")
        elif url and not url.startswith("https://"):
            errors.append(f"{where}: url is not http(s): {url}")
        urls.setdefault(url, []).append(doc_id)
        date = doc.get("date")
        if date is not None:
            s = str(date)
            if not (_is_iso_date(s) or re.fullmatch(r"\d{4}(-\d{2})?", s)):
                warnings.append(f"{where}: non-ISO date {s!r}")
        status = doc.get("status")
        if status is None:
            warnings.append(f"{where}: no status (treated as unknown)")
        elif status not in statuses:
            errors.append(f"{where}: unknown status {status!r}; allowed: {sorted(statuses)}")
        for j, rel in enumerate(doc.get("relations") or []):
            rw = f"{where}.relations[{j}]"
            if not isinstance(rel, dict):
                errors.append(f"{rw}: not an object")
                continue
            if rel.get("type") not in rel_types:
                errors.append(f"{rw}: unknown relation type {rel.get('type')!r}")
            target = str(rel.get("target", ""))
            if not target:
                errors.append(f"{rw}: missing target")
            elif not target.startswith("urn:lex:") and target not in {str(d.get("id")) for d in docs if isinstance(d, dict)}:
                errors.append(f"{rw}: target {target!r} is neither a document id nor a urn:lex")
        by_category.setdefault(str(doc.get("category", "")), []).append(doc)

    for url, owners in urls.items():
        if url and len(owners) > 1:
            warnings.append(f"duplicate url {url} used by {owners}")

    core = by_category.get("redata_core_law", [])
    if core and not any(d.get("status") == Status.IN_FORCE.value for d in core):
        errors.append("redata_core_law has no document with status=in_force (only lapsed/proposed?)")
    for cat, rows in by_category.items():
        if all(r.get("status") in (Status.LAPSED.value, Status.REVOKED.value, Status.ARCHIVED.value) for r in rows) and rows[0].get("status"):
            warnings.append(f"category {cat!r}: every document is lapsed/revoked/archived")

    summary = {
        "documents": len(docs),
        "categories": {k: len(v) for k, v in sorted(by_category.items())},
        "statuses": {},
    }
    for doc in docs:
        if isinstance(doc, dict):
            s = str(doc.get("status", "unknown"))
            summary["statuses"][s] = summary["statuses"].get(s, 0) + 1
    return errors, warnings, summary
