#!/usr/bin/env python3
"""Build the SCN Phase 3 policy corpus (Scenario 2).

Sweeps Brazilian legislative, regulatory, state and municipal sources for
data-center policy, merges them under LexML ``urn:lex`` keys, infers the
lifecycle relations (MP -> PL -> Lei -> Decreto), and scores recall against
the curated gold set in ``docs/phase3_redata_policy_corpus.json``.

Usage (from the repository root, inside the .venv):

    python scripts/build_policy_corpus.py sweep --since 2024-01-01
    python scripts/build_policy_corpus.py sweep --connectors camara,senado --no-detail
    python scripts/build_policy_corpus.py sweep --offline          # replay from cache
    python scripts/build_policy_corpus.py sweep --dry-run          # list planned requests
    python scripts/build_policy_corpus.py validate-corpus docs/phase3_redata_policy_corpus.json
    python scripts/build_policy_corpus.py recall                   # re-score an existing output dir
    python scripts/build_policy_corpus.py list-connectors

Outputs land in ``clean_data/sovereign_compute_nexus/phase3_policy_corpus/``:
``documents.jsonl`` (canonical), ``documents.parquet`` (if pyarrow),
``relations.jsonl``, ``corpus.sqlite`` (FTS5), ``recall_report.json``,
``candidates_for_curation.jsonl`` and ``manifest.json``.

Network note: ``*.leg.br`` / ``gov.br`` hosts must be reachable from the
machine running this; the cache under ``<out>/raw_cache`` makes reruns and
``--offline`` replays deterministic.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from policy_corpus import __version__  # noqa: E402
from policy_corpus.connectors import Context, registry  # noqa: E402
from policy_corpus.fetch import Fetcher, OfflineMiss  # noqa: E402
from policy_corpus.linking import (  # noqa: E402
    canonicalize_relations,
    curated_relations,
    link_same_bill_across_houses,
    mirror_enacted,
    replaces_pairs,
    status_audit,
)
from policy_corpus.models import ConnectorResult, Document, Relation, dedupe_documents  # noqa: E402
from policy_corpus.recall import compute_recall  # noqa: E402
from policy_corpus.store import read_jsonl, write_jsonl, write_outputs  # noqa: E402
from policy_corpus.terms import QUERY_TERMS, TERMS  # noqa: E402
from policy_corpus.validate import validate_corpus  # noqa: E402

DEFAULT_OUT = ROOT / "clean_data" / "sovereign_compute_nexus" / "phase3_policy_corpus"
DEFAULT_GOLD = ROOT / "docs" / "phase3_redata_policy_corpus.json"
DEFAULT_SOURCES = ROOT / "scripts" / "policy_corpus" / "sources.json"
DEFAULT_STATES = ["CE", "SP", "MG", "RS"]

log = logging.getLogger("policy_corpus")


# ----------------------------------------------------------------- helpers
def load_dotenv(path: Path) -> int:
    """Minimal KEY=VALUE loader (no python-dotenv dependency). Existing
    environment wins. Returns the number of keys set."""
    if not path.exists():
        return 0
    n = 0
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            n += 1
    return n


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date: {value!r}") from exc


def load_sources(path: Path) -> dict:
    if not path.exists():
        log.warning("sources config %s not found; using empty config", path)
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_gold(path: Path) -> list[dict]:
    if not path.exists():
        log.warning("gold corpus %s not found; recall will be skipped", path)
        return []
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return list(data.get("documents") or [])


# ------------------------------------------------------------------- sweep
def run_sweep(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    config = load_sources(args.sources)
    out_dir: Path = args.out
    cache_dir: Path = args.cache_dir or (out_dir / "raw_cache")
    fetcher = Fetcher(
        cache_dir=cache_dir,
        offline=args.offline,
        dry_run=args.dry_run,
        timeout=args.timeout,
        host_throttle={k: float(v) for k, v in (config.get("host_throttle_seconds") or {}).items()},
        refresh=args.refresh,
    )
    states = [s.strip().upper() for s in args.states.split(",") if s.strip()]
    ctx = Context(
        fetcher=fetcher,
        since=args.since,
        until=args.until,
        config=config,
        states=states,
        log=log,
        query_terms=QUERY_TERMS,
        max_pages=args.max_pages,
        detail=not args.no_detail,
        include_unverified=args.include_unverified,
        options={
            "dou_days": args.dou_days,
            "dou_sections": [s.strip().upper() for s in args.dou_sections.split(",") if s.strip()],
            "sapl_hosts": args.sapl_host or [],
            "municipalities": args.municipality or [],
        },
    )
    specs = registry()
    wanted = [c.strip() for c in args.connectors.split(",") if c.strip()] if args.connectors else list(specs)
    unknown = [c for c in wanted if c not in specs]
    if unknown:
        log.error("unknown connectors: %s (available: %s)", unknown, ", ".join(specs))
        return 2

    results: list[ConnectorResult] = []
    for name in wanted:
        spec = specs[name]
        if spec.gated and not args.include_unverified:
            res = ConnectorResult(name=name, verified_adapter=False)
            res.notes.append("skipped: unverified adapter (pass --include-unverified to run)")
            results.append(res)
            log.info("[%s] skipped (unverified)", name)
            continue
        log.info("[%s] %s", name, spec.description)
        try:
            res = spec.run(ctx)
        except OfflineMiss as exc:
            res = ConnectorResult(name=name)
            res.errors.append(f"offline miss: {exc}")
        except Exception as exc:  # one broken adapter must not kill the sweep
            log.exception("[%s] crashed", name)
            res = ConnectorResult(name=name)
            res.errors.append(f"crashed: {type(exc).__name__}: {exc}")
        if spec.verification != "live":
            for d in res.documents:
                d.extra.setdefault("adapter_verification", spec.verification)
        results.append(res)
        log.info("[%s] %d documents, %d relations, %d errors", name, len(res.documents), len(res.relations), len(res.errors))
        for err in res.errors:
            log.warning("[%s] %s", name, err)

    all_docs: list[Document] = [d for r in results for d in r.documents]
    relations: list[Relation] = [rel for r in results for rel in r.relations]
    documents, merged = dedupe_documents(all_docs)
    gold = load_gold(args.gold)

    relations += link_same_bill_across_houses(documents)
    relations += replaces_pairs(documents, config.get("replaces_pairs") or [])
    relations += mirror_enacted(relations)
    relations += curated_relations(gold)

    recall = compute_recall(gold, documents) if gold else None
    gold_hit_keys = {h["corpus_key"] for h in (recall or {}).get("hit_detail", [])}
    aliases: dict[str, str] = {}
    gold_by_id = {str(g.get("id")): g for g in gold}
    for h in (recall or {}).get("hit_detail", []):
        aliases[f"gold:{h['gold_id']}"] = h["corpus_key"]
        gold_urn = gold_by_id.get(h["gold_id"], {}).get("urn")
        if gold_urn:
            aliases[gold_urn] = h["corpus_key"]
    unique_rel = canonicalize_relations(relations, documents, aliases)
    candidates = [d for d in documents if d.match_strength == "high" and d.key not in gold_hit_keys and d.verified_adapter]

    by = lambda attr: _count(documents, attr)  # noqa: E731
    manifest = {
        "package_version": __version__,
        "python": platform.python_version(),
        "run": {
            "since": args.since.isoformat(),
            "until": args.until.isoformat(),
            "connectors": wanted,
            "states": states,
            "offline": args.offline,
            "dry_run": args.dry_run,
            "detail": not args.no_detail,
            "include_unverified": args.include_unverified,
            "sources_config": str(args.sources),
            "gold": str(args.gold),
        },
        "terms": {"query_terms": list(QUERY_TERMS), "acceptance_terms": [{"name": t.name, "precision": t.precision} for t in TERMS]},
        "connectors": [r.summary() for r in results],
        "fetcher": {"requests_made": fetcher.requests_made, "cache_hits": fetcher.cache_hits, "cache_dir": str(cache_dir), "planned_requests": len(fetcher.planned)},
        "counts": {
            "documents_raw": len(all_docs),
            "documents_unique": len(documents),
            "merged_duplicates": merged,
            "relations": len(unique_rel),
            "by_source": by("source"),
            "by_layer": by("layer"),
            "by_status": by("status"),
            "by_jurisdiction": by("jurisdiction"),
            "candidates_for_curation": len(candidates),
        },
        "status_audit": status_audit(documents),
        "recall": {k: v for k, v in (recall or {}).items() if k not in ("hit_detail", "miss_detail")} if recall else None,
    }
    if args.dry_run:
        manifest["planned_requests"] = fetcher.planned[:500]

    paths = write_outputs(out_dir, documents, unique_rel, manifest, sqlite=not args.no_sqlite)
    if recall:
        (out_dir / "recall_report.json").write_text(json.dumps(recall, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["recall_report"] = str(out_dir / "recall_report.json")
    write_jsonl(out_dir / "candidates_for_curation.jsonl", [d.to_record() for d in candidates])
    paths["candidates"] = str(out_dir / "candidates_for_curation.jsonl")

    print(json.dumps({"documents": len(documents), "relations": len(unique_rel), "recall": (recall or {}).get("recall"), "outputs": paths}, indent=2, ensure_ascii=False))
    errors_total = sum(len(r.errors) for r in results)
    if not documents and errors_total and not args.dry_run:
        log.error("no documents and %d connector errors — treat this run as failed", errors_total)
        return 1
    return 0


def _count(docs: list[Document], attr: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in docs:
        v = getattr(d, attr)
        v = v.value if hasattr(v, "value") else str(v)
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- validate
def run_validate(args: argparse.Namespace) -> int:
    errors, warnings, summary = validate_corpus(args.path)
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"{len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


# ------------------------------------------------------------------ recall
def run_recall(args: argparse.Namespace) -> int:
    docs_path = args.out / "documents.jsonl"
    if not docs_path.exists():
        log.error("%s not found; run a sweep first", docs_path)
        return 2
    rows = read_jsonl(docs_path)
    documents = []
    for r in rows:
        r = dict(r)
        r.pop("key", None)
        documents.append(Document(**r))
    gold = load_gold(args.gold)
    report = compute_recall(gold, documents)
    (args.out / "recall_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("hit_detail",)}, indent=2, ensure_ascii=False))
    return 0


def run_list(_args: argparse.Namespace) -> int:
    for name, spec in registry().items():
        gate = " (needs --include-unverified)" if spec.gated else ""
        creds = " [credentials]" if spec.needs_credentials else ""
        print(f"{name:15s} {spec.verification:14s}{creds}{gate}\n{'':15s} {spec.description}")
    return 0


# -------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("sweep", help="run the connectors and build the corpus")
    s.add_argument("--since", type=parse_date, default=dt.date(2024, 1, 1))
    s.add_argument("--until", type=parse_date, default=dt.date.today())
    s.add_argument("--connectors", default="", help="comma list; default all registered")
    s.add_argument("--states", default=",".join(DEFAULT_STATES))
    s.add_argument("--out", type=Path, default=DEFAULT_OUT)
    s.add_argument("--cache-dir", type=Path, default=None)
    s.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    s.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    s.add_argument("--offline", action="store_true", help="serve everything from cache; fail on misses")
    s.add_argument("--dry-run", action="store_true", help="record planned requests, fetch nothing")
    s.add_argument("--refresh", action="store_true", help="ignore cached responses")
    s.add_argument("--no-detail", action="store_true", help="skip per-item detail calls (Câmara)")
    s.add_argument("--max-pages", type=int, default=50)
    s.add_argument("--timeout", type=float, default=30.0)
    s.add_argument("--dou-days", type=int, default=30, help="INLABS: how many days back from --until")
    s.add_argument("--dou-sections", default="DO1,DO1E")
    s.add_argument("--sapl-host", action="append", help="repeatable: https://sapl.<municipio>.<uf>.leg.br")
    s.add_argument("--municipality", action="append", help="repeatable: extra 7-digit IBGE code for Querido Diário")
    s.add_argument("--include-unverified", action="store_true", help="also run adapters marked unverified")
    s.add_argument("--no-sqlite", action="store_true")
    s.set_defaults(func=run_sweep)

    v = sub.add_parser("validate-corpus", help="validate the curated Phase 3 corpus JSON")
    v.add_argument("path", type=Path, nargs="?", default=DEFAULT_GOLD)
    v.set_defaults(func=run_validate)

    r = sub.add_parser("recall", help="re-score recall for an existing output directory")
    r.add_argument("--out", type=Path, default=DEFAULT_OUT)
    r.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    r.set_defaults(func=run_recall)

    l = sub.add_parser("list-connectors", help="show connectors and their verification level")
    l.set_defaults(func=run_list)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO), format="%(levelname)s %(name)s: %(message)s")
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    if getattr(args, "since", None) and getattr(args, "until", None) and args.since > args.until:
        parser.error(f"--since {args.since} is after --until {args.until}")
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
