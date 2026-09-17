#!/usr/bin/env python3
"""Re-evaluate Phase 4 feasibility, frontier and shortlist from the scored table.

Why this exists
---------------
`run_scn_phase4_optimization.py` originally read each distance as
``float(row.get(field, np.inf) or np.inf)``. ``0.0`` is falsy in Python, so a cell
sitting exactly on a transmission line (0 km) was treated as infinitely far and
excluded. The script now uses ``as_float`` (None/NaN-safe), but its outputs in
``clean_data/sovereign_compute_nexus/phase4_optimization/`` still came from the
pre-fix run of May 29, 2026.

The six objectives and the resilience score are computed vectorized in pandas and
never carried the defect, so they are reused unchanged from ``phase4_all_h3_scored.csv``.
Only the columns downstream of the hard-constraint test are recomputed here, using the
same functions as the main script:

  phase4_exclusion_reasons, phase4_feasible, phase4_decision_label, phase4_rank

and the derived files: feasible_candidates, pareto_frontier (csv + geojson),
recommended_sites, summary.json, and the two charts. The folium inspector map needs
geopandas/folium and is not regenerated here; rerun the main script for that.

Usage
-----
  python3 scripts/rerun_phase4_feasibility.py --check      # reproduce the archived pre-fix run (must match exactly)
  python3 scripts/rerun_phase4_feasibility.py              # write corrected outputs
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "clean_data" / "sovereign_compute_nexus" / "phase4_optimization"
CHART_DIR = OUT_DIR / "charts"
PHASE3_GEOJSON = ROOT / "clean_data" / "sovereign_compute_nexus" / "phase3_policy" / "phase3_policy_penalties.geojson"
OBJECTIVE_COLS = [
    "objective_grid_cost",
    "objective_latency_cost",
    "objective_water_land_risk",
    "objective_policy_burden",
    "objective_energy_shortfall",
    "objective_curtailment_shortfall",
]


# ---- identical to run_scn_phase4_optimization.py -------------------------------------------
def as_float(value, default: float) -> float:
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out):
        return default
    return out


def as_float_prefix(value, default: float) -> float:
    """The defective coercion, kept only so --check can reproduce the archived run."""
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "t")
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return False
    return bool(v)


def exclusion_reasons(row: pd.Series, args, coerce) -> str:
    reasons: list[str] = []
    if not truthy(row.get("fsor_allowed_phase3", False)):
        reasons.append("phase3_fsor_disallowed")
    if truthy(row.get("policy_hard_exclusion", False)):
        reasons.append("phase3_policy_hard_exclusion")
    if coerce(row.get("p_deg_rs"), 1.0) > args.epsilon:
        reasons.append("p_deg_rs_above_epsilon")
    if coerce(row.get("nearest_hv_ons_bus_km"), np.inf) > args.max_hv_bus_km:
        reasons.append("too_far_from_hv_bus")
    if coerce(row.get("nearest_ons_line_km"), np.inf) > args.max_line_km:
        reasons.append("too_far_from_ons_line")
    if coerce(row.get("nearest_idc_km"), np.inf) > args.max_idc_km:
        reasons.append("too_far_from_idc_anchor")
    if coerce(row.get("nearby_renewable_mw"), 0.0) < args.min_renewable_mw:
        reasons.append("insufficient_nearby_renewables")
    if args.exclude_human_review and truthy(row.get("human_review_required", False)):
        reasons.append("human_review_required")
    return ";".join(reasons) or "passes_phase4_hard_constraints"


def pareto_frontier(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return candidates.copy()
    values = candidates[OBJECTIVE_COLS].to_numpy(dtype=float)
    is_frontier = np.ones(values.shape[0], dtype=bool)
    for idx, candidate in enumerate(values):
        if not is_frontier[idx]:
            continue
        dominated = np.all(values <= candidate, axis=1) & np.any(values < candidate, axis=1)
        if dominated.any():
            is_frontier[idx] = False
    return candidates.loc[is_frontier].copy().sort_values("phase4_resilience_score", ascending=False)


def recommended_sites(frontier: pd.DataFrame, limit: int) -> pd.DataFrame:
    if frontier.empty:
        return frontier.copy()
    rec = frontier.sort_values(
        ["phase4_resilience_score", "objective_policy_burden", "objective_water_land_risk", "objective_grid_cost"],
        ascending=[False, True, True, True],
    ).head(limit).copy()
    rec["phase4_rank"] = range(1, len(rec) + 1)
    rec["phase4_decision_label"] = np.where(rec["human_review_required"].map(truthy), "frontier_with_human_review", "frontier_preferred")
    return rec


# ---- charts (same figures as write_charts in the main script) --------------------------------
def write_charts(candidates, frontier, recommendations) -> dict[str, str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    plt.figure(figsize=(9, 6))
    plt.scatter(candidates["objective_grid_cost"], candidates["objective_latency_cost"], c=candidates["phase4_resilience_score"], cmap="viridis", s=18, alpha=0.55, label="Feasible H3 cells")
    plt.scatter(frontier["objective_grid_cost"], frontier["objective_latency_cost"], c="crimson", s=42, edgecolor="white", linewidth=0.5, label="Pareto frontier")
    plt.scatter(recommendations["objective_grid_cost"], recommendations["objective_latency_cost"], c="gold", s=80, marker="*", edgecolor="black", linewidth=0.5, label="Recommended shortlist")
    plt.xlabel("Grid interconnection cost, normalized"); plt.ylabel("IDC latency/proximity cost, normalized")
    plt.title("Phase 4 Frontier: Grid Access vs. Latency"); plt.colorbar(label="Phase 4 resilience score"); plt.legend(loc="best"); plt.tight_layout()
    p = CHART_DIR / "phase4_frontier_grid_vs_latency.png"; plt.savefig(p, dpi=180); plt.close(); paths["grid_vs_latency"] = str(p)

    plt.figure(figsize=(9, 6))
    plt.scatter(1 - candidates["objective_energy_shortfall"], candidates["objective_water_land_risk"], c=candidates["objective_policy_burden"], cmap="magma_r", s=18, alpha=0.55, label="Feasible H3 cells")
    plt.scatter(1 - frontier["objective_energy_shortfall"], frontier["objective_water_land_risk"], c="cyan", s=42, edgecolor="black", linewidth=0.5, label="Pareto frontier")
    plt.scatter(1 - recommendations["objective_energy_shortfall"], recommendations["objective_water_land_risk"], c="gold", s=80, marker="*", edgecolor="black", linewidth=0.5, label="Recommended shortlist")
    plt.xlabel("Renewable opportunity score, normalized"); plt.ylabel("Water-land risk, normalized")
    plt.title("Phase 4 Frontier: Energy Opportunity vs. Land-Water Risk"); plt.colorbar(label="Policy burden, normalized"); plt.legend(loc="best"); plt.tight_layout()
    p = CHART_DIR / "phase4_frontier_energy_vs_risk.png"; plt.savefig(p, dpi=180); plt.close(); paths["energy_vs_risk"] = str(p)
    return paths


def update_geojson(path: Path, props_by_id: dict[str, dict], keep_ids: set[str] | None) -> int:
    """Rewrite feature properties in place (geometry untouched). keep_ids filters features."""
    g = json.loads(path.read_text(encoding="utf-8"))
    feats = []
    for f in g["features"]:
        hid = f["properties"].get("h3_id")
        if keep_ids is not None and hid not in keep_ids:
            continue
        if hid in props_by_id:
            f["properties"].update(props_by_id[hid])
        feats.append(f)
    g["features"] = feats
    path.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    return len(feats)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epsilon", type=float, default=0.62)
    ap.add_argument("--max-hv-bus-km", type=float, default=25.0)
    ap.add_argument("--max-line-km", type=float, default=15.0)
    ap.add_argument("--max-idc-km", type=float, default=50.0)
    ap.add_argument("--min-renewable-mw", type=float, default=0.0)
    ap.add_argument("--recommendation-count", type=int, default=25)
    ap.add_argument("--exclude-human-review", action="store_true")
    ap.add_argument("--check", action="store_true", help="Reproduce the pre-fix coercion and compare with the stored outputs; write nothing.")
    ap.add_argument("--source", default=str(OUT_DIR / "phase4_all_h3_scored.csv"), help="Scored table to re-evaluate.")
    args = ap.parse_args()

    gdf = pd.read_csv(args.source, low_memory=False)
    coerce = as_float_prefix if args.check else as_float
    gdf["phase4_exclusion_reasons"] = gdf.apply(exclusion_reasons, axis=1, args=(args, coerce))
    gdf["phase4_feasible"] = gdf["phase4_exclusion_reasons"].eq("passes_phase4_hard_constraints")
    candidates = gdf[gdf["phase4_feasible"]].copy()
    frontier = pareto_frontier(candidates)
    recommendations = recommended_sites(frontier, args.recommendation_count)

    frontier["phase4_decision_label"] = "pareto_frontier"
    frontier.loc[frontier["human_review_required"].map(truthy), "phase4_decision_label"] = "pareto_frontier_human_review"
    candidates["phase4_decision_label"] = "feasible_candidate"
    gdf["phase4_decision_label"] = np.where(gdf["phase4_feasible"], "feasible_candidate", "excluded_by_hard_constraint")
    gdf.loc[gdf["h3_id"].isin(frontier["h3_id"]), "phase4_decision_label"] = "pareto_frontier"
    gdf.loc[gdf["h3_id"].isin(recommendations["h3_id"]), "phase4_decision_label"] = "recommended_shortlist"
    final = gdf.set_index("h3_id")["phase4_decision_label"]
    candidates["phase4_decision_label"] = candidates["h3_id"].map(final)
    frontier["phase4_decision_label"] = frontier["h3_id"].map(final)

    n_feas, n_front, n_rec = len(candidates), len(frontier), len(recommendations)
    n_rev_front = int(frontier["human_review_required"].map(truthy).sum())
    n_rev_rec = int(recommendations["human_review_required"].map(truthy).sum())
    print(f"feasible {n_feas}  frontier {n_front}  recommended {n_rec}  review: frontier {n_rev_front}, recommended {n_rev_rec}  "
          f"top score {recommendations['phase4_resilience_score'].max():.2f}")

    if args.check:
        stored = pd.read_csv(OUT_DIR / "phase4_all_h3_scored.csv", low_memory=False).set_index("h3_id")
        rec_stored = pd.read_csv(OUT_DIR / "phase4_recommended_sites.csv").set_index("h3_id")
        g = gdf.set_index("h3_id")
        ok_feas = (g["phase4_feasible"] == stored["phase4_feasible"].map(truthy)).all()
        ok_lab = (g["phase4_decision_label"] == stored["phase4_decision_label"]).all()
        ok_reason = (g["phase4_exclusion_reasons"] == stored["phase4_exclusion_reasons"]).all()
        r = recommendations.set_index("h3_id")["phase4_rank"]
        ok_rank = r.index.equals(rec_stored.index) and (r.values == rec_stored["phase4_rank"].values).all()
        print("check vs stored -> feasible:", ok_feas, "| labels:", ok_lab, "| reasons:", ok_reason, "| shortlist order:", ok_rank)
        raise SystemExit(0 if (ok_feas and ok_lab and ok_reason and ok_rank) else 1)

    # ---- write outputs (same file set as write_outputs, minus the folium map) ----
    all_csv = OUT_DIR / "phase4_all_h3_scored.csv"
    gdf.to_csv(all_csv, index=False)
    candidates.sort_values("phase4_resilience_score", ascending=False).to_csv(OUT_DIR / "phase4_feasible_candidates.csv", index=False)
    frontier.to_csv(OUT_DIR / "phase4_pareto_frontier.csv", index=False)
    recommendations.to_csv(OUT_DIR / "phase4_recommended_sites.csv", index=False)

    props = {row.h3_id: {"phase4_exclusion_reasons": row.phase4_exclusion_reasons, "phase4_feasible": bool(row.phase4_feasible), "phase4_decision_label": row.phase4_decision_label}
             for row in gdf[["h3_id", "phase4_exclusion_reasons", "phase4_feasible", "phase4_decision_label"]].itertuples(index=False)}
    n_all = update_geojson(OUT_DIR / "phase4_all_h3_scored.geojson", props, None)
    # frontier geojson: rebuild from the all-cells geometry so newly admitted cells are included
    src = json.loads((OUT_DIR / "phase4_all_h3_scored.geojson").read_text(encoding="utf-8"))
    front_ids = set(frontier["h3_id"])
    fg = dict(src); fg["features"] = [f for f in src["features"] if f["properties"].get("h3_id") in front_ids]
    (OUT_DIR / "phase4_pareto_frontier.geojson").write_text(json.dumps(fg, ensure_ascii=False), encoding="utf-8")

    chart_paths = write_charts(candidates, frontier, recommendations)

    reason_counts = gdf.loc[~gdf["phase4_feasible"], "phase4_exclusion_reasons"].str.split(";").explode().value_counts().to_dict()
    top_cols = ["phase4_rank", "h3_id", "center_lat", "center_lon", "phase4_decision_label", "phase4_resilience_score", "p_deg_rs", "legal_stringency",
                "human_review_required", "nearest_hv_ons_bus_km", "nearest_ons_line_km", "nearest_idc_km", "nearby_renewable_mw", "state_curtailed_mwh"]
    summary_path = OUT_DIR / "phase4_summary.json"
    old = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    summary = {
        "case_study": "sovereign_compute_nexus_phase4_constrained_optimization",
        "method_note": ("Discrete H3 constrained multi-objective optimizer. Phase 3 feasibility and "
                        "Sentinel-derived Phase 2 degradation risk are enforced as hard constraints before Pareto sorting."),
        "phase3_source": str(PHASE3_GEOJSON),
        "h3_cells": int(len(gdf)),
        "phase4_feasible_cells": n_feas,
        "pareto_frontier_cells": n_front,
        "recommended_sites": n_rec,
        "human_review_frontier_cells": n_rev_front,
        "human_review_recommended_sites": n_rev_rec,
        "constraints": {"p_deg_rs_epsilon": args.epsilon, "max_hv_bus_km": args.max_hv_bus_km, "max_line_km": args.max_line_km, "max_idc_km": args.max_idc_km,
                        "min_renewable_mw": args.min_renewable_mw, "exclude_human_review": bool(args.exclude_human_review),
                        "fsor_allowed_phase3": "hard constraint", "policy_hard_exclusion": "hard constraint"},
        "objectives_minimized": OBJECTIVE_COLS,
        "exclusion_reason_counts": {str(k): int(v) for k, v in reason_counts.items()},
        "top_recommendations": recommendations[top_cols].round(4).to_dict(orient="records"),
        "outputs": {
            "all_scored_csv": str(all_csv), "all_scored_geojson": str(OUT_DIR / "phase4_all_h3_scored.geojson"),
            "feasible_candidates_csv": str(OUT_DIR / "phase4_feasible_candidates.csv"),
            "pareto_frontier_csv": str(OUT_DIR / "phase4_pareto_frontier.csv"), "pareto_frontier_geojson": str(OUT_DIR / "phase4_pareto_frontier.geojson"),
            "recommended_sites_csv": str(OUT_DIR / "phase4_recommended_sites.csv"),
            "interactive_map": (old.get("outputs") or {}).get("interactive_map"),
            "charts": chart_paths, "summary_json": str(summary_path),
        },
        "regeneration_note": ("Feasibility, frontier, shortlist, labels, charts and this summary were regenerated on 2026-09-17 with the "
                              "None/NaN-safe distance coercion (as_float). Objectives and scores are unchanged from the 2026-05-29 run. "
                              "The folium inspector map still reflects the pre-fix run; rerun run_scn_phase4_optimization.py to refresh it. "
                              "Pre-fix outputs are archived under _archive/phase4_prefix_run_20260529/."),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {n_all} features to all-cells GeoJSON, {len(fg['features'])} to frontier GeoJSON; charts: {list(chart_paths)}")


if __name__ == "__main__":
    main()
