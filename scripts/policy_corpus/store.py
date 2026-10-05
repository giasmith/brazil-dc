"""Outputs: JSONL (always), Parquet (when pyarrow is importable), SQLite with
FTS5 full-text index (when the local SQLite has FTS5), and the run manifest.

JSONL is the canonical artefact because it round-trips ``extra`` (nested
dicts) losslessly; Parquet flattens ``extra`` to a JSON string column.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from .models import Document, Relation

log = logging.getLogger("policy_corpus.store")

PARQUET_COLUMNS = [
    "key", "urn", "source", "layer", "jurisdiction", "doc_type", "number", "year", "date",
    "title", "ementa", "issuer", "house", "url", "text_url", "status", "text_variant",
    "match_strength", "matched_terms", "retrieved_at", "http_status", "sha256_bytes",
    "sha256_text", "encoding", "verified_adapter", "extra",
]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: {exc}") from exc
    return rows


def write_parquet(path: Path, rows: list[dict[str, Any]]) -> bool:
    """Return True when written; False (with a log line) when pyarrow is absent."""
    try:
        import pandas as pd  # noqa: WPS433
        import pyarrow  # noqa: F401
    except ImportError as exc:
        log.warning("parquet skipped (%s); JSONL is the canonical output", exc)
        return False
    flat = []
    for r in rows:
        f = {k: r.get(k) for k in PARQUET_COLUMNS}
        f["matched_terms"] = "|".join(r.get("matched_terms") or [])
        f["extra"] = json.dumps(r.get("extra") or {}, ensure_ascii=False, default=str)
        flat.append(f)
    df = pd.DataFrame(flat, columns=PARQUET_COLUMNS)
    # Explicit dtypes so an all-null column does not become float/object at random.
    for col in ("year", "http_status"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    df["verified_adapter"] = df["verified_adapter"].astype("boolean")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return True


def write_sqlite(path: Path, docs: list[dict[str, Any]], relations: list[dict[str, Any]]) -> bool:
    """Documents + relations tables and an FTS5 index over title/ementa/text
    excerpt. Returns False if FTS5 is unavailable (tables are still written)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    try:
        con.execute(
            "CREATE TABLE documents (key TEXT PRIMARY KEY, urn TEXT, source TEXT, layer TEXT, jurisdiction TEXT, "
            "doc_type TEXT, number TEXT, year INTEGER, date TEXT, title TEXT, ementa TEXT, issuer TEXT, house TEXT, "
            "url TEXT, text_url TEXT, status TEXT, text_variant TEXT, match_strength TEXT, matched_terms TEXT, "
            "retrieved_at TEXT, http_status INTEGER, sha256_bytes TEXT, sha256_text TEXT, encoding TEXT, "
            "verified_adapter INTEGER, extra TEXT)"
        )
        con.execute("CREATE TABLE relations (src TEXT, dst TEXT, type TEXT, evidence TEXT, source TEXT)")
        con.executemany(
            "INSERT OR REPLACE INTO documents VALUES (" + ",".join("?" * 26) + ")",
            [
                (
                    d["key"], d.get("urn"), d.get("source"), d.get("layer"), d.get("jurisdiction"), d.get("doc_type"),
                    d.get("number"), d.get("year"), d.get("date"), d.get("title"), d.get("ementa"), d.get("issuer"),
                    d.get("house"), d.get("url"), d.get("text_url"), d.get("status"), d.get("text_variant"),
                    d.get("match_strength"), "|".join(d.get("matched_terms") or []), d.get("retrieved_at"),
                    d.get("http_status"), d.get("sha256_bytes"), d.get("sha256_text"), d.get("encoding"),
                    1 if d.get("verified_adapter") else 0, json.dumps(d.get("extra") or {}, ensure_ascii=False, default=str),
                )
                for d in docs
            ],
        )
        con.executemany(
            "INSERT INTO relations VALUES (?,?,?,?,?)",
            [(r["src"], r["dst"], r["type"], r.get("evidence"), r.get("source")) for r in relations],
        )
        fts_ok = True
        try:
            con.execute("CREATE VIRTUAL TABLE documents_fts USING fts5(key UNINDEXED, title, ementa, excerpt)")
            con.executemany(
                "INSERT INTO documents_fts VALUES (?,?,?,?)",
                [(d["key"], d.get("title") or "", d.get("ementa") or "", str((d.get("extra") or {}).get("text_excerpt") or "")) for d in docs],
            )
        except sqlite3.OperationalError as exc:
            fts_ok = False
            log.warning("FTS5 unavailable (%s); documents/relations tables written without full-text index", exc)
        con.commit()
    finally:
        con.close()
    return fts_ok


def write_outputs(out_dir: Path, documents: list[Document], relations: list[Relation], manifest: dict[str, Any], sqlite: bool = True) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc_rows = [d.to_record() for d in documents]
    rel_rows = [r.to_record() for r in relations]
    paths = {
        "documents_jsonl": out_dir / "documents.jsonl",
        "relations_jsonl": out_dir / "relations.jsonl",
        "manifest_json": out_dir / "manifest.json",
    }
    write_jsonl(paths["documents_jsonl"], doc_rows)
    write_jsonl(paths["relations_jsonl"], rel_rows)
    if write_parquet(out_dir / "documents.parquet", doc_rows):
        paths["documents_parquet"] = out_dir / "documents.parquet"
    if sqlite:
        write_sqlite(out_dir / "corpus.sqlite", doc_rows, rel_rows)
        paths["corpus_sqlite"] = out_dir / "corpus.sqlite"
    manifest = dict(manifest)
    manifest["outputs"] = {k: str(v) for k, v in paths.items()}
    manifest["written_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    paths["manifest_json"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}
