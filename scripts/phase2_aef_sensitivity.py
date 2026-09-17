"""Sensitivity of Phase 2 degradation scores to the AlphaEarth change-signal weight.

    python3 scripts/phase2_aef_sensitivity.py
    python3 scripts/phase2_aef_sensitivity.py --weights 0 0.1 0.25 0.5 --top 100

Re-runs the Phase 2 feature stack once, then recomputes p_deg_rs at each weight (no files from
Phase 2 are overwritten). Only *land candidates* are compared: cells that are not hard-excluded
and have < 50% water, i.e. the cells the solver can actually choose. Reports, for each weight
against weight 0:

    spearman     rank correlation of p_deg_rs over land candidates (1 = identical ranking)
    top_overlap  share of the top-N highest-risk land cells that are the same
    fray_in_top  how many of the top-N are boundary-fray cells (next to a protected/Indigenous edge)
    mean / p95   distribution of p_deg_rs over land candidates

Writes clean_data/sovereign_compute_nexus/phase2_rs/phase2_aef_sensitivity.{csv,json}.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_scn_phase2_rs as p2  # noqa: E402

DEFAULT_AEF = p2.OUT_DIR / "alphaearth_h3_ceara_case_study_box_r8.parquet"


def build_features(sentinel: Path, aef: Path, edge_decay_km: float, pca_components: int):
    gdf = p2.load_baseline()
    gdf = p2.add_lulc_labels(gdf)
    gdf = p2.add_fray_flags(gdf)
    gdf = p2.nearest_constraint_context(gdf)
    gdf = p2.add_rs_feature_proxies(gdf, edge_decay_km)
    gdf, _ = p2.add_pca_autoencoder_proxy(gdf, pca_components)
    gdf, sentinel_meta = p2.add_sentinel_features(gdf, sentinel)
    if not sentinel_meta.get("enabled"):
        raise SystemExit(f"Sentinel features not found at {sentinel}")
    gdf, aef_meta = p2.add_alphaearth_features(gdf, aef, 0.0)
    if not aef_meta.get("enabled"):
        raise SystemExit(f"AlphaEarth features not found at {aef}")
    return gdf


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights", type=float, nargs="+", default=[0.0, 0.1, 0.25, 0.5])
    ap.add_argument("--top", type=int, default=100, help="size of the top-N set compared across weights")
    ap.add_argument("--sentinel-features", type=Path, default=p2.DEFAULT_SENTINEL_FEATURES)
    ap.add_argument("--alphaearth-features", type=Path, default=DEFAULT_AEF)
    ap.add_argument("--edge-decay-km", type=float, default=5.0)
    ap.add_argument("--pca-components", type=int, default=3)
    args = ap.parse_args()
    if any(not 0 <= w <= 1 for w in args.weights):
        ap.error("weights must be in [0, 1]")
    if 0.0 not in args.weights:
        args.weights = [0.0] + list(args.weights)

    base = build_features(args.sentinel_features, args.alphaearth_features, args.edge_decay_km, args.pca_components)
    water = pd.to_numeric(base["water_surface_share_proxy"], errors="coerce").fillna(0)
    land = ~base["hard_exclusion"].astype(bool) & (water < 0.5)
    n_land = int(land.sum())
    if n_land < args.top:
        raise SystemExit(f"only {n_land} land candidates; lower --top")
    print(f"{len(base):,} cells, {n_land:,} land candidates (not excluded, < 50% water)")
    print(f"AlphaEarth change signal on land candidates: mean {base.loc[land, 'rs_aef_change_signal'].mean():.3f}, "
          f"corr with edge exposure {base.loc[land, ['rs_aef_change_signal', 'rs_edge_exposure']].corr().iloc[0, 1]:+.2f}, "
          f"corr with optical stress {base.loc[land, ['rs_aef_change_signal', 'rs_sentinel_optical_stress']].corr().iloc[0, 1]:+.2f}")

    scores: dict[float, pd.Series] = {}
    for w in sorted(set(args.weights)):
        scored = p2.add_degradation_probability(base, use_sentinel=True, aef_weight=w)
        scores[w] = scored.loc[land, "p_deg_rs"]

    ref = scores[0.0]
    top_ref = set(ref.nlargest(args.top).index)
    fray = base.loc[land, "boundary_fray_cell"].astype(bool)
    rows = []
    for w, s in scores.items():
        top = set(s.nlargest(args.top).index)
        rows.append({
            "aef_weight": w,
            "spearman_vs_w0": round(float(s.rank().corr(ref.rank())), 4),
            f"top{args.top}_overlap_vs_w0": round(len(top & top_ref) / args.top, 3),
            f"fray_cells_in_top{args.top}": int(fray.loc[list(top)].sum()),
            "mean_p_deg": round(float(s.mean()), 4),
            "p95_p_deg": round(float(s.quantile(0.95)), 4),
            "cells_at_or_above_0.7": int((s >= 0.7).sum()),
        })
    table = pd.DataFrame(rows)
    print("\n" + table.to_string(index=False))

    out = p2.OUT_DIR / "phase2_aef_sensitivity"
    table.to_csv(out.with_suffix(".csv"), index=False)
    out.with_suffix(".json").write_text(json.dumps({
        "land_candidates": n_land, "top_n": args.top, "weights": sorted(set(args.weights)),
        "sentinel_features": str(args.sentinel_features), "alphaearth_features": str(args.alphaearth_features),
        "rows": rows,
    }, indent=2))
    print(f"\nwrote {out.relative_to(p2.ROOT)}.csv / .json")

    # the cells that move the most: useful for a figure or a spot check in the map
    if len(scores) > 1:
        w_max = max(scores)
        delta = (scores[w_max].rank() - ref.rank())
        movers = base.loc[delta.abs().nlargest(10).index, ["h3_id", "class_name", "boundary_fray_cell", "nearest_constraint_km", "rs_aef_change_signal", "rs_sentinel_optical_stress"]].copy()
        movers["rank_w0"] = ref.rank(ascending=False).loc[movers.index].astype(int)
        movers[f"rank_w{w_max}"] = scores[w_max].rank(ascending=False).loc[movers.index].astype(int)
        print(f"\nLargest rank moves between w=0 and w={w_max} (rank 1 = highest degradation risk)")
        print(movers.round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
