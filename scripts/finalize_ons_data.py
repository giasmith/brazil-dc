"""Finalize the ONS raw-data folder so every dataset is Parquet and time coverage matches.

Run from the repo root on a machine with network access to ONS:

    python3 scripts/finalize_ons_data.py            # download + convert + inventory
    python3 scripts/finalize_ons_data.py --dry-run  # show what would happen

What it does
  1. Downloads CARGA_ENERGIA (daily load) and BALANCO_ENERGIA_SUBSISTEMA (hourly
     balance) for 2021-2024 so they match the 2021-10 -> 2026-05 curtailment series.
     Prefers ONS's Parquet where published; otherwise converts the CSV.
  2. Converts every remaining curtailment CSV month to Parquet with explicit dtypes,
     checks the columns against an ONS-published Parquet month of the same dataset,
     and moves the CSV originals to _archive/ons_csv_originals_<date>/ (never deletes).
  3. Removes the empty data/ons/geracao_usina_2_ho/ folder if it is still empty.
  4. Writes data/ons/ons_inventory.json (file, rows, first/last timestamp).

Safe to re-run: existing Parquet files are never rewritten.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

try:
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover
    sys.exit("pyarrow is required: pip install pyarrow")

ROOT = Path(__file__).resolve().parents[1]
ONS_DIR = ROOT / "data" / "ons"
ARCHIVE_DIR = ROOT / "_archive" / f"ons_csv_originals_{date.today():%Y%m%d}"
S3_BASE = "https://ons-aws-prod-opendata.s3.amazonaws.com/"
S3_NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
YEARS_TO_ADD = [2021, 2022, 2023, 2024]

ANNUAL_DATASETS = {
    "carga_energia_di": "CARGA_ENERGIA",
    "balanco_energia_subsistema_ho": "BALANCO_ENERGIA_SUBSISTEMA",
}
MONTHLY_DATASETS = {
    "restricao_coff_eolica_tm": "RESTRICAO_COFF_EOLICA",
    "restricao_coff_eolica_detail_tm": "RESTRICAO_COFF_EOLICA_DETAIL",
    "restricao_coff_fotovoltaica_tm": "RESTRICAO_COFF_FOTOVOLTAICA",
    "restricao_coff_fotovoltaica_detail_tm": "RESTRICAO_COFF_FOTOVOLTAICA_DETAIL",
}


# ----------------------------------------------------------------- S3 helpers
def list_s3_keys(prefix: str) -> list[str]:
    keys: list[str] = []
    continuation = None
    while True:
        url = f"{S3_BASE}?list-type=2&prefix={prefix}"
        if continuation:
            url += f"&continuation-token={continuation}"
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        keys.extend(k.find("s:Key", S3_NS).text for k in root.findall("s:Contents", S3_NS))
        token = root.find("s:NextContinuationToken", S3_NS)
        if token is None:
            break
        continuation = token.text
    return keys


def fetch_bytes(key: str) -> bytes:
    response = requests.get(S3_BASE + key, timeout=300)
    response.raise_for_status()
    return response.content


# --------------------------------------------------------------- CSV parsing
def read_ons_csv(source: Path | bytes) -> pd.DataFrame:
    """ONS CSVs are ';'-separated, UTF-8 with BOM, and sometimes use ',' decimals."""
    raw = source if isinstance(source, bytes) else source.read_bytes()
    sample = raw[:4096].decode("utf-8-sig", errors="replace")
    sep = ";" if sample.count(";") >= sample.count(",") else ","
    df = pd.read_csv(BytesIO(raw), sep=sep, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def type_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Apply ONS naming conventions: din_/dat_ -> datetime, val_ -> float, flg_ -> float, rest -> string."""
    out = df.copy()
    for col in out.columns:
        s = out[col]
        if col.startswith(("din_", "dat_")):
            out[col] = pd.to_datetime(s.replace("", pd.NA), errors="coerce")
        elif col.startswith(("val_", "flg_")):
            out[col] = pd.to_numeric(
                s.str.strip().str.replace(",", ".", regex=False).replace({"": pd.NA, "-": pd.NA}),
                errors="coerce",
            )
        else:
            out[col] = s.str.strip().astype("string")
    return out


def schema_reference(local_dir: Path, exclude: Path | None = None) -> list[str] | None:
    """Columns of an ONS-published Parquet month in the same folder, for a sanity check."""
    for p in sorted(local_dir.glob("*.parquet")):
        if p != exclude and not p.name.startswith("DicionarioDados"):
            return parquet_columns(p)
    return None


def parquet_columns(path: Path) -> list[str]:
    """Column names from the Parquet footer only (no data read). Raises if the file is not valid Parquet."""
    return list(pq.read_schema(str(path)).names)


def warn_schema(name: str, cols: list[str], ref: list[str] | None) -> None:
    if ref is None:
        return
    missing, extra = set(ref) - set(cols), set(cols) - set(ref)
    if missing or extra:
        print(f"  WARNING {name}: columns differ from published Parquet. missing={sorted(missing)} extra={sorted(extra)}")


# ------------------------------------------------------------------- steps
def step_download_annual(dry_run: bool) -> None:
    print("\n[1/4] Load and balance files for", YEARS_TO_ADD)
    for folder, stem in ANNUAL_DATASETS.items():
        local_dir = ONS_DIR / folder
        local_dir.mkdir(parents=True, exist_ok=True)
        keys = list_s3_keys(f"dataset/{folder}/")
        for year in YEARS_TO_ADD:
            target = local_dir / f"{stem}_{year}.parquet"
            if target.exists():
                print(f"  have  {target.relative_to(ROOT)}")
                continue
            parquet_key = next((k for k in keys if k.endswith(f"{stem}_{year}.parquet")), None)
            csv_key = next((k for k in keys if k.endswith(f"{stem}_{year}.csv")), None)
            if not parquet_key and not csv_key:
                print(f"  MISSING on ONS: {stem}_{year} (checked {len(keys)} keys)")
                continue
            print(f"  fetch {parquet_key or csv_key} -> {target.relative_to(ROOT)}")
            if dry_run:
                continue
            if parquet_key:
                target.write_bytes(fetch_bytes(parquet_key))
            else:
                type_columns(read_ons_csv(fetch_bytes(csv_key))).to_parquet(target, index=False)
            try:
                cols = parquet_columns(target)
            except Exception as exc:  # noqa: BLE001
                head = target.read_bytes()[:80]
                target.unlink()
                raise RuntimeError(f"{target.name} is not valid Parquet ({exc}); first bytes: {head!r}") from exc
            warn_schema(target.name, cols, schema_reference(local_dir, exclude=target))


def step_convert_monthly_csvs(dry_run: bool) -> None:
    print("\n[2/4] Convert curtailment CSV months to Parquet")
    for folder in MONTHLY_DATASETS:
        local_dir = ONS_DIR / folder
        if not local_dir.exists():
            continue
        csvs = sorted(local_dir.glob("*.csv"))
        if not csvs:
            print(f"  {folder}: already all Parquet")
            continue
        ref = schema_reference(local_dir)
        for csv_path in csvs:
            target = csv_path.with_suffix(".parquet")
            if target.exists():
                print(f"  skip  {csv_path.name} (Parquet exists)")
            else:
                print(f"  conv  {csv_path.name}")
                if not dry_run:
                    df = type_columns(read_ons_csv(csv_path))
                    if df.empty:
                        print(f"  WARNING {csv_path.name} parsed to 0 rows; left as CSV")
                        continue
                    warn_schema(csv_path.name, list(df.columns), ref)
                    df.to_parquet(target, index=False)
                    back = pd.read_parquet(target)
                    if len(back) != len(df):
                        raise RuntimeError(f"Row count mismatch after writing {target}")
            if not dry_run and target.exists():
                dest_dir = ARCHIVE_DIR / folder
                dest_dir.mkdir(parents=True, exist_ok=True)
                csv_path.rename(dest_dir / csv_path.name)


def step_remove_empty(dry_run: bool) -> None:
    print("\n[3/4] Empty folders")
    empty = ONS_DIR / "geracao_usina_2_ho"
    if empty.exists() and not any(empty.iterdir()):
        print(f"  remove {empty.relative_to(ROOT)}")
        if not dry_run:
            empty.rmdir()
    else:
        print("  nothing to remove")


def step_inventory(dry_run: bool) -> None:
    print("\n[4/4] Inventory")
    rows = []
    for p in sorted(ONS_DIR.rglob("*")):
        if p.suffix not in {".parquet", ".csv"} or p.name.startswith("DicionarioDados"):
            continue
        entry = {"path": str(p.relative_to(ROOT)), "format": p.suffix[1:], "bytes": p.stat().st_size}
        try:
            if p.suffix == ".parquet":
                cols = parquet_columns(p)
                tcol = next((c for c in cols if c.startswith(("din_", "dat_"))), None)
                entry["rows"] = int(pq.ParquetFile(str(p)).metadata.num_rows)
                tseries = pd.read_parquet(p, columns=[tcol])[tcol] if tcol else None
            else:
                df = read_ons_csv(p)
                tcol = next((c for c in df.columns if c.startswith(("din_", "dat_"))), None)
                entry["rows"] = int(len(df))
                tseries = df[tcol] if tcol else None
            if tseries is not None:
                t = pd.to_datetime(tseries, errors="coerce")
                entry["first"], entry["last"] = str(t.min()), str(t.max())
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
        rows.append(entry)
    leftover = [r["path"] for r in rows if r["format"] == "csv"]
    print(f"  {len(rows)} data files, {len(leftover)} still CSV")
    if not dry_run:
        (ONS_DIR / "ons_inventory.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
        print(f"  wrote {ONS_DIR.relative_to(ROOT)}/ons_inventory.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-download", action="store_true", help="only convert local CSVs")
    args = parser.parse_args()
    if not args.skip_download:
        step_download_annual(args.dry_run)
    step_convert_monthly_csvs(args.dry_run)
    step_remove_empty(args.dry_run)
    step_inventory(args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
