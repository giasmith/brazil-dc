"""Merge AlphaEarth per-H3 CSV exports (from request_alphaearth_h3_exports.py) into one feature table.

    python3 scripts/ingest_alphaearth_h3.py --drive-dir "~/Library/CloudStorage/GoogleDrive-<acct>/My Drive/SCN_GEE_AlphaEarth"
    python3 scripts/ingest_alphaearth_h3.py --drive-dir ... --region ceara_case_study_box --h3-resolution 8

Writes clean_data/sovereign_compute_nexus/phase2_rs/alphaearth_h3_<region>_r<res>.{parquet,csv} with, per h3_id:

    aef_<year>_A00..A63      renormalised mean embedding (unit length) for each year
    aef_<year>_cos_prev      mean per-pixel cosine similarity with the previous year (NaN for the first year)
    aef_<year>_change_prev   1 - cos_prev  (0 = identical, higher = more change)
    aef_change_span          1 - cosine(mean vector first year, mean vector last year)
    aef_change_prev_max      largest single-year change across the requested years
    aef_pc1..pc3             first three principal components of the latest year's embedding (for maps only)
    aef_n_pixels_<year>      pixel count behind each mean (small counts = partial cell coverage)

The cosine-based change columns are the "did this place's satellite signature move" signal that
Phase 2 can use as an edge-effect feature. The embedding axes themselves are not interpretable.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "clean_data" / "sovereign_compute_nexus" / "phase2_rs"
BANDS = [f"A{i:02d}" for i in range(64)]
NAME_RE = re.compile(r"^aef_(?P<region>.+)_r(?P<res>\d{1,2})_(?P<year>\d{4})_c(?P<chunk>\d{3})\.csv$")
MIN_PIXELS_WARN = 50  # a full resolution-8 cell at 10 m has ~7,400 pixels; below this the mean is unreliable


def discover(drive_dir: Path, region: str | None, res: int | None) -> pd.DataFrame:
    rows = []
    for path in sorted(drive_dir.glob("aef_*.csv")):
        m = NAME_RE.match(path.name)
        if not m:
            continue
        rows.append({"path": path, "region": m["region"], "res": int(m["res"]), "year": int(m["year"]), "chunk": int(m["chunk"])})
    files = pd.DataFrame(rows)
    if files.empty:
        raise SystemExit(f"No aef_*_r*_<year>_c*.csv files in {drive_dir}")
    if region:
        files = files[files["region"] == region]
    if res is not None:
        files = files[files["res"] == res]
    combos = files[["region", "res"]].drop_duplicates()
    if len(combos) != 1:
        raise SystemExit(f"Several region/resolution combinations found; pick one with --region/--h3-resolution:\n{combos.to_string(index=False)}")
    return files


def read_year(files: pd.DataFrame, year: int) -> pd.DataFrame:
    parts = [pd.read_csv(p) for p in files.loc[files["year"] == year, "path"]]
    df = pd.concat(parts, ignore_index=True)
    # EE adds system:index and .geo columns; drop anything we did not ask for
    keep = ["h3_id"] + BANDS + ["cos_prev", "n_pixels"]
    missing = [c for c in keep if c not in df.columns and c != "cos_prev"]
    if missing:
        raise SystemExit(f"{year}: export is missing columns {missing[:5]}...")
    if "cos_prev" not in df.columns:
        df["cos_prev"] = np.nan
    df = df[keep].copy()
    dup = df["h3_id"].duplicated().sum()
    if dup:
        print(f"  {year}: {dup} duplicate h3_id rows (overlapping chunks?); keeping first")
        df = df.drop_duplicates("h3_id")
    # EE writes empty strings for masked cells; coerce
    for c in BANDS + ["cos_prev", "n_pixels"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.set_index("h3_id")


def renormalise(vecs: np.ndarray) -> np.ndarray:
    """Mean of unit vectors shrinks toward the origin; put it back on the unit sphere."""
    norm = np.linalg.norm(vecs, axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(norm > 0, vecs / norm, np.nan)
    return out


def pca3(vecs: np.ndarray) -> np.ndarray:
    ok = ~np.isnan(vecs).any(axis=1)
    out = np.full((len(vecs), 3), np.nan)
    if ok.sum() < 4:
        return out
    x = vecs[ok] - vecs[ok].mean(axis=0)
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    out[ok] = x @ vt[:3].T
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drive-dir", required=True, type=Path, help="Folder where Drive synced the aef_*.csv exports")
    ap.add_argument("--region", help="Region label used in the export names, e.g. ceara_case_study_box or state_CE")
    ap.add_argument("--h3-resolution", type=int)
    args = ap.parse_args()

    drive_dir = args.drive_dir.expanduser()
    if not drive_dir.is_dir():
        raise SystemExit(f"Not a directory: {drive_dir}")
    files = discover(drive_dir, args.region, args.h3_resolution)
    region, res = files["region"].iloc[0], int(files["res"].iloc[0])
    years = [int(y) for y in sorted(files["year"].unique())]
    print(f"{region} r{res}: {len(files)} files, years {years}")

    # chunk completeness: every year should have the same chunk ids
    chunk_sets = {y: set(files.loc[files["year"] == y, "chunk"]) for y in years}
    all_chunks = set().union(*chunk_sets.values())
    for y, s in chunk_sets.items():
        if s != all_chunks:
            print(f"  WARNING {y}: missing chunks {sorted(all_chunks - s)} (tasks still running or failed?)")

    per_year = {y: read_year(files, y) for y in years}
    index = sorted(set().union(*(df.index for df in per_year.values())))
    cols: dict[str, np.ndarray] = {}
    means: dict[int, np.ndarray] = {}
    for y in years:
        df = per_year[y].reindex(index)
        vec = renormalise(df[BANDS].to_numpy(dtype=float))
        means[y] = vec
        for j, b in enumerate(BANDS):
            cols[f"aef_{y}_{b}"] = vec[:, j]
        cos_prev = df["cos_prev"].to_numpy(dtype=float)
        cols[f"aef_{y}_cos_prev"] = cos_prev
        cols[f"aef_{y}_change_prev"] = np.clip(1.0 - cos_prev, 0.0, 2.0)
        cols[f"aef_n_pixels_{y}"] = df["n_pixels"].to_numpy()
        low = int((df["n_pixels"] < MIN_PIXELS_WARN).sum())
        nan_cells = int(np.isnan(vec).any(axis=1).sum())
        print(f"  {y}: {len(df):,} cells, {nan_cells} without embedding, {low} with < {MIN_PIXELS_WARN} pixels")

    first, last = years[0], years[-1]
    span_cos = np.einsum("ij,ij->i", means[first], means[last])
    # cosine of two unit vectors is in [-1, 1] up to rounding; anything further off means a bad renormalisation
    if np.nanmax(np.abs(span_cos)) > 1 + 1e-6:
        raise RuntimeError("cosine similarity outside [-1, 1]; embeddings were not unit length after renormalisation")
    cols["aef_change_span"] = np.clip(1.0 - span_cos, 0.0, 2.0)
    prev = np.column_stack([cols[f"aef_{y}_change_prev"] for y in years[1:]]) if len(years) > 1 else None
    cols["aef_change_prev_max"] = np.nanmax(prev, axis=1) if prev is not None else np.full(len(index), np.nan)
    pcs = pca3(means[last])
    for k in range(3):
        cols[f"aef_pc{k + 1}"] = pcs[:, k]
    out = pd.DataFrame(cols, index=pd.Index(index, name="h3_id"))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = OUT_DIR / f"alphaearth_h3_{region}_r{res}"
    out.reset_index().to_parquet(stem.with_suffix(".parquet"), index=False)
    out.reset_index().to_csv(stem.with_suffix(".csv"), index=False)
    print(f"wrote {stem.relative_to(ROOT)}.parquet / .csv  ({len(out):,} cells, {out.shape[1]} columns)")
    print(f"  change_span: median {out['aef_change_span'].median():.4f}, 95th pct {out['aef_change_span'].quantile(0.95):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
