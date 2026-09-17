from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

os.environ.setdefault("XDG_CACHE_HOME", "/private/tmp")
os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/matplotlib")
import matplotlib.pyplot as plt

from map_ui import add_bottom_detail_panel


ROOT = Path(__file__).resolve().parents[1]
SCN_DIR = ROOT / "clean_data" / "sovereign_compute_nexus"
PHASE3_GEOJSON = SCN_DIR / "phase3_policy" / "phase3_policy_penalties.geojson"
OUT_DIR = SCN_DIR / "phase4_optimization"
CHART_DIR = OUT_DIR / "charts"


OBJECTIVE_COLS = [
    "objective_grid_cost",
    "objective_latency_cost",
    "objective_water_land_risk",
    "objective_policy_burden",
    "objective_energy_shortfall",
    "objective_curtailment_shortfall",
]


def normalize_minimize(series: pd.Series, cap_quantile: float = 0.95) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() == 0:
        return pd.Series(0.0, index=series.index)
    fill = float(values.quantile(cap_quantile))
    values = values.fillna(fill)
    lo = float(values.min())
    hi = float(values.quantile(cap_quantile))
    if math.isclose(lo, hi):
        return pd.Series(0.0, index=series.index)
    return ((values.clip(upper=hi) - lo) / (hi - lo)).clip(0, 1)


def normalize_maximize(series: pd.Series, cap_quantile: float = 0.95) -> pd.Series:
    return 1 - normalize_minimize(series, cap_quantile=cap_quantile)


def as_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.lower().isin(["true", "1", "yes"])


def load_phase3() -> gpd.GeoDataFrame:
    if not PHASE3_GEOJSON.exists():
        raise SystemExit("Missing Phase 3 output. Run scripts/run_scn_phase3_policy.py first.")
    gdf = gpd.read_file(PHASE3_GEOJSON).to_crs("EPSG:4326")
    bool_cols = [
        "protected_overlap",
        "indigenous_overlap",
        "hard_exclusion",
        "outside_state_boundary",
        "boundary_fray_cell",
        "protected_fray_cell",
        "indigenous_fray_cell",
        "policy_hard_exclusion",
        "fsor_allowed_phase3",
        "human_review_required",
        "rs_sentinel_available",
    ]
    for col in bool_cols:
        if col in gdf.columns:
            gdf[col] = as_bool(gdf[col])
    return gdf


def add_phase4_objectives(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    grid_cost = (
        0.58 * normalize_minimize(gdf["nearest_hv_ons_bus_km"])
        + 0.42 * normalize_minimize(gdf["nearest_ons_line_km"])
    ).clip(0, 1)
    latency_cost = normalize_minimize(gdf["nearest_idc_km"])
    energy_shortfall = normalize_maximize(gdf["nearby_renewable_mw"])
    curtailment_shortfall = normalize_maximize(gdf["state_curtailed_mwh"])

    p_deg = pd.to_numeric(gdf["p_deg_rs"], errors="coerce").fillna(1).clip(0, 1)
    water = pd.to_numeric(gdf["water_surface_share_proxy"], errors="coerce").fillna(0).clip(0, 1)
    lulc = pd.to_numeric(gdf["rs_lulc_vulnerability"], errors="coerce").fillna(0.5).clip(0, 1)
    # gdf.get(col, 0) returns a bare int when the column is absent, and an int has no .fillna,
    # so a --force-proxy run (no Sentinel table) used to crash here. Missing Sentinel evidence is
    # scored neutral (0.5), the same value a NaN gets, rather than 0, which would read as "best possible".
    sentinel_signal = (
        gdf["rs_sentinel_signal"] if "rs_sentinel_signal" in gdf.columns
        else pd.Series(0.5, index=gdf.index)
    )
    sentinel = pd.to_numeric(sentinel_signal, errors="coerce").fillna(0.5).clip(0, 1)
    water_land_risk = (0.48 * p_deg + 0.22 * water + 0.18 * lulc + 0.12 * sentinel).clip(0, 1)

    lambda_norm = normalize_minimize(gdf["lambda_policy"], cap_quantile=1.0)
    review = gdf["human_review_required"].astype(float) if "human_review_required" in gdf.columns else 0
    fray = gdf["boundary_fray_cell"].astype(float) if "boundary_fray_cell" in gdf.columns else 0
    near_constraint = 1 - normalize_minimize(gdf["nearest_constraint_km"])
    policy_burden = (0.48 * lambda_norm + 0.24 * review + 0.18 * fray + 0.10 * near_constraint).clip(0, 1)

    gdf["objective_grid_cost"] = grid_cost
    gdf["objective_latency_cost"] = latency_cost
    gdf["objective_energy_shortfall"] = energy_shortfall
    gdf["objective_curtailment_shortfall"] = curtailment_shortfall
    gdf["objective_water_land_risk"] = water_land_risk
    gdf["objective_policy_burden"] = policy_burden
    gdf["phase4_resilience_score"] = (
        100
        * (
            0.24 * (1 - gdf["objective_grid_cost"])
            + 0.17 * (1 - gdf["objective_latency_cost"])
            + 0.18 * (1 - gdf["objective_energy_shortfall"])
            + 0.10 * (1 - gdf["objective_curtailment_shortfall"])
            + 0.20 * (1 - gdf["objective_water_land_risk"])
            + 0.11 * (1 - gdf["objective_policy_burden"])
        )
    ).round(2)
    gdf["phase4_energy_opportunity_score"] = (100 * (1 - gdf["objective_energy_shortfall"])).round(2)
    gdf["phase4_grid_access_score"] = (100 * (1 - gdf["objective_grid_cost"])).round(2)
    gdf["phase4_land_water_safety_score"] = (100 * (1 - gdf["objective_water_land_risk"])).round(2)
    return gdf


def as_float(value, default: float) -> float:
    """Coerce a row field to float, substituting ``default`` only when the value is
    genuinely missing (None / NaN / empty string).

    Do not write ``float(x or default)`` for this: ``0.0`` is falsy in Python, so a
    cell sitting exactly on a transmission line (distance 0.0 km) or with a
    degradation probability of exactly 0.0 would be silently replaced by the
    default and rejected. Phase 4 runs before 2026-09-15 carried that defect.
    """
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out):
        return default
    return out


def exclusion_reasons(row: pd.Series, args: argparse.Namespace) -> str:
    reasons: list[str] = []
    if not bool(row.get("fsor_allowed_phase3", False)):
        reasons.append("phase3_fsor_disallowed")
    if bool(row.get("policy_hard_exclusion", False)):
        reasons.append("phase3_policy_hard_exclusion")
    if as_float(row.get("p_deg_rs"), 1.0) > args.epsilon:
        reasons.append("p_deg_rs_above_epsilon")
    if as_float(row.get("nearest_hv_ons_bus_km"), np.inf) > args.max_hv_bus_km:
        reasons.append("too_far_from_hv_bus")
    if as_float(row.get("nearest_ons_line_km"), np.inf) > args.max_line_km:
        reasons.append("too_far_from_ons_line")
    if as_float(row.get("nearest_idc_km"), np.inf) > args.max_idc_km:
        reasons.append("too_far_from_idc_anchor")
    if as_float(row.get("nearby_renewable_mw"), 0.0) < args.min_renewable_mw:
        reasons.append("insufficient_nearby_renewables")
    if args.exclude_human_review and bool(row.get("human_review_required", False)):
        reasons.append("human_review_required")
    return ";".join(reasons) or "passes_phase4_hard_constraints"


def apply_hard_constraints(gdf: gpd.GeoDataFrame, args: argparse.Namespace) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    gdf = gdf.copy()
    gdf["phase4_exclusion_reasons"] = gdf.apply(exclusion_reasons, axis=1, args=(args,))
    gdf["phase4_feasible"] = gdf["phase4_exclusion_reasons"].eq("passes_phase4_hard_constraints")
    candidates = gdf[gdf["phase4_feasible"]].copy()
    return gdf, candidates


def pareto_frontier(candidates: gpd.GeoDataFrame, objective_cols: list[str]) -> gpd.GeoDataFrame:
    if candidates.empty:
        return candidates.copy()
    values = candidates[objective_cols].to_numpy(dtype=float)
    is_frontier = np.ones(values.shape[0], dtype=bool)
    for idx, candidate in enumerate(values):
        if not is_frontier[idx]:
            continue
        dominated_by_other = np.all(values <= candidate, axis=1) & np.any(values < candidate, axis=1)
        if dominated_by_other.any():
            is_frontier[idx] = False
    frontier = candidates.loc[is_frontier].copy()
    return frontier.sort_values("phase4_resilience_score", ascending=False)


def recommended_sites(frontier: gpd.GeoDataFrame, limit: int) -> gpd.GeoDataFrame:
    if frontier.empty:
        return frontier.copy()
    recommendations = frontier.sort_values(
        [
            "phase4_resilience_score",
            "objective_policy_burden",
            "objective_water_land_risk",
            "objective_grid_cost",
        ],
        ascending=[False, True, True, True],
    ).head(limit).copy()
    recommendations["phase4_rank"] = range(1, len(recommendations) + 1)
    recommendations["phase4_decision_label"] = np.where(
        recommendations["human_review_required"].astype(bool),
        "frontier_with_human_review",
        "frontier_preferred",
    )
    return recommendations


def write_charts(candidates: gpd.GeoDataFrame, frontier: gpd.GeoDataFrame, recommendations: gpd.GeoDataFrame) -> dict[str, str]:
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    if candidates.empty:
        return paths

    plt.figure(figsize=(9, 6))
    plt.scatter(
        candidates["objective_grid_cost"],
        candidates["objective_latency_cost"],
        c=candidates["phase4_resilience_score"],
        cmap="viridis",
        s=18,
        alpha=0.55,
        label="Feasible H3 cells",
    )
    if not frontier.empty:
        plt.scatter(
            frontier["objective_grid_cost"],
            frontier["objective_latency_cost"],
            c="crimson",
            s=42,
            edgecolor="white",
            linewidth=0.5,
            label="Pareto frontier",
        )
    if not recommendations.empty:
        plt.scatter(
            recommendations["objective_grid_cost"],
            recommendations["objective_latency_cost"],
            c="gold",
            s=80,
            marker="*",
            edgecolor="black",
            linewidth=0.5,
            label="Recommended shortlist",
        )
    plt.xlabel("Grid interconnection cost, normalized")
    plt.ylabel("IDC latency/proximity cost, normalized")
    plt.title("Phase 4 Frontier: Grid Access vs. Latency")
    plt.colorbar(label="Phase 4 resilience score")
    plt.legend(loc="best")
    plt.tight_layout()
    path = CHART_DIR / "phase4_frontier_grid_vs_latency.png"
    plt.savefig(path, dpi=180)
    plt.close()
    paths["grid_vs_latency"] = str(path)

    plt.figure(figsize=(9, 6))
    energy_opportunity = 1 - candidates["objective_energy_shortfall"]
    plt.scatter(
        energy_opportunity,
        candidates["objective_water_land_risk"],
        c=candidates["objective_policy_burden"],
        cmap="magma_r",
        s=18,
        alpha=0.55,
        label="Feasible H3 cells",
    )
    if not frontier.empty:
        plt.scatter(
            1 - frontier["objective_energy_shortfall"],
            frontier["objective_water_land_risk"],
            c="cyan",
            s=42,
            edgecolor="black",
            linewidth=0.5,
            label="Pareto frontier",
        )
    if not recommendations.empty:
        plt.scatter(
            1 - recommendations["objective_energy_shortfall"],
            recommendations["objective_water_land_risk"],
            c="gold",
            s=80,
            marker="*",
            edgecolor="black",
            linewidth=0.5,
            label="Recommended shortlist",
        )
    plt.xlabel("Renewable opportunity score, normalized")
    plt.ylabel("Water-land risk, normalized")
    plt.title("Phase 4 Frontier: Energy Opportunity vs. Land-Water Risk")
    plt.colorbar(label="Policy burden, normalized")
    plt.legend(loc="best")
    plt.tight_layout()
    path = CHART_DIR / "phase4_frontier_energy_vs_risk.png"
    plt.savefig(path, dpi=180)
    plt.close()
    paths["energy_vs_risk"] = str(path)
    return paths


def write_map(gdf: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame, frontier: gpd.GeoDataFrame, recommendations: gpd.GeoDataFrame) -> str | None:
    try:
        import folium
        from branca.colormap import LinearColormap
    except ModuleNotFoundError:
        return None

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    center = [float(gdf["center_lat"].median()), float(gdf["center_lon"].median())]
    fmap = folium.Map(location=center, zoom_start=10, tiles="CartoDB positron", control_scale=True)
    cmap = LinearColormap(
        ["#f7fcf5", "#c7e9c0", "#74c476", "#238b45", "#00441b"],
        vmin=0,
        vmax=100,
        caption="Phase 4 resilience score",
    )

    panel_fields = [
        "h3_id",
        "phase4_decision_label",
        "phase4_resilience_score",
        "phase4_feasible",
        "legal_stringency",
        "fsor_allowed_phase3",
        "human_review_required",
        "p_deg_rs",
        "lambda_policy",
        "policy_reason",
        "nearest_hv_ons_bus_km",
        "nearest_ons_line_km",
        "nearest_idc_km",
        "nearby_renewable_mw",
        "state_curtailed_mwh",
        "phase4_grid_access_score",
        "phase4_energy_opportunity_score",
        "phase4_land_water_safety_score",
        "objective_policy_burden",
        "phase4_exclusion_reasons",
    ]

    def style_all(feature):
        props = feature["properties"]
        if not props.get("phase4_feasible"):
            return {"fillColor": "#d9d9d9", "color": "#9ca3af", "weight": 0.18, "fillOpacity": 0.14}
        return {"fillColor": "#deebf7", "color": "#9ecae1", "weight": 0.2, "fillOpacity": 0.16}

    inspector_layers = []
    all_layer = folium.GeoJson(
        gdf[panel_fields + ["geometry"]],
        name="All H3 cells and Phase 4 constraints",
        style_function=style_all,
        tooltip=folium.GeoJsonTooltip(fields=["h3_id", "phase4_feasible", "phase4_resilience_score"]),
    ).add_to(fmap)
    inspector_layers.append(all_layer)

    if not candidates.empty:
        candidate_layer = folium.GeoJson(
            candidates[panel_fields + ["geometry"]],
            name="Phase 4 feasible candidates",
            style_function=lambda feature: {
                "fillColor": cmap(float(feature["properties"].get("phase4_resilience_score", 0))),
                "color": "#2563eb",
                "weight": 0.45,
                "fillOpacity": 0.42,
            },
            tooltip=folium.GeoJsonTooltip(fields=["h3_id", "phase4_resilience_score", "p_deg_rs"]),
        ).add_to(fmap)
        inspector_layers.append(candidate_layer)

    if not frontier.empty:
        frontier_layer = folium.GeoJson(
            frontier[panel_fields + ["geometry"]],
            name="Phase 4 Pareto frontier",
            style_function=lambda feature: {
                "fillColor": "#ef4444",
                "color": "#7f1d1d",
                "weight": 1.6,
                "fillOpacity": 0.64,
            },
            tooltip=folium.GeoJsonTooltip(fields=["h3_id", "phase4_resilience_score", "p_deg_rs"]),
        ).add_to(fmap)
        inspector_layers.append(frontier_layer)

    if not recommendations.empty:
        recommendation_layer = folium.GeoJson(
            recommendations[panel_fields + ["phase4_rank", "geometry"]],
            name="Recommended shortlist",
            style_function=lambda feature: {
                "fillColor": "#facc15",
                "color": "#713f12",
                "weight": 2.2,
                "fillOpacity": 0.78,
            },
            tooltip=folium.GeoJsonTooltip(fields=["phase4_rank", "h3_id", "phase4_resilience_score"]),
        ).add_to(fmap)
        inspector_layers.append(recommendation_layer)

    add_bottom_detail_panel(
        fmap,
        inspector_layers,
        title="Phase 4 Constrained Optimization Inspector",
        subtitle=(
            "Click an H3 cell to inspect hard feasibility, Sentinel degradation risk, policy burden, "
            "grid access, energy opportunity, and final Pareto status."
        ),
        metric_fields=[
            "phase4_resilience_score",
            "phase4_decision_label",
            "p_deg_rs",
            "legal_stringency",
            "phase4_grid_access_score",
            "phase4_energy_opportunity_score",
        ],
        detail_fields=panel_fields,
        label_map={
            "h3_id": "H3 ID",
            "phase4_decision_label": "Phase 4 Decision",
            "phase4_resilience_score": "Resilience Score",
            "phase4_feasible": "Hard Constraints Pass",
            "legal_stringency": "Legal Stringency",
            "fsor_allowed_phase3": "FSOR Allowed",
            "human_review_required": "Human Review",
            "p_deg_rs": "Sentinel P_deg(x)",
            "lambda_policy": "Policy Weight",
            "policy_reason": "Policy Trigger Logic",
            "nearest_hv_ons_bus_km": "Nearest HV Bus",
            "nearest_ons_line_km": "Nearest ONS Line",
            "nearest_idc_km": "Nearest IDC",
            "nearby_renewable_mw": "Nearby Renewable MW",
            "state_curtailed_mwh": "State Curtailed MWh",
            "phase4_grid_access_score": "Grid Access Score",
            "phase4_energy_opportunity_score": "Energy Opportunity",
            "phase4_land_water_safety_score": "Land-Water Safety",
            "objective_policy_burden": "Policy Burden",
            "phase4_exclusion_reasons": "Exclusion Reasons",
        },
        panel_id="phase4-optimization-inspector",
    )
    cmap.add_to(fmap)
    folium.LayerControl(collapsed=False).add_to(fmap)
    map_path = OUT_DIR / "phase4_optimization_map.html"
    fmap.save(map_path)
    return str(map_path)


def write_outputs(
    gdf: gpd.GeoDataFrame,
    candidates: gpd.GeoDataFrame,
    frontier: gpd.GeoDataFrame,
    recommendations: gpd.GeoDataFrame,
    args: argparse.Namespace,
) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_geojson = OUT_DIR / "phase4_all_h3_scored.geojson"
    all_csv = OUT_DIR / "phase4_all_h3_scored.csv"
    candidates_csv = OUT_DIR / "phase4_feasible_candidates.csv"
    frontier_csv = OUT_DIR / "phase4_pareto_frontier.csv"
    frontier_geojson = OUT_DIR / "phase4_pareto_frontier.geojson"
    recommended_csv = OUT_DIR / "phase4_recommended_sites.csv"
    summary_path = OUT_DIR / "phase4_summary.json"

    gdf.to_file(all_geojson, driver="GeoJSON")
    gdf.drop(columns="geometry", errors="ignore").to_csv(all_csv, index=False)
    candidates.drop(columns="geometry", errors="ignore").sort_values("phase4_resilience_score", ascending=False).to_csv(candidates_csv, index=False)
    frontier.drop(columns="geometry", errors="ignore").to_csv(frontier_csv, index=False)
    recommendations.drop(columns="geometry", errors="ignore").to_csv(recommended_csv, index=False)
    if not frontier.empty:
        frontier.to_file(frontier_geojson, driver="GeoJSON")
    elif frontier_geojson.exists():
        frontier_geojson.unlink()

    chart_paths = write_charts(candidates, frontier, recommendations)
    map_path = write_map(gdf, candidates, frontier, recommendations)

    reason_counts = (
        gdf.loc[~gdf["phase4_feasible"], "phase4_exclusion_reasons"]
        .str.split(";")
        .explode()
        .value_counts()
        .to_dict()
    )

    top_cols = [
        "phase4_rank",
        "h3_id",
        "center_lat",
        "center_lon",
        "phase4_decision_label",
        "phase4_resilience_score",
        "p_deg_rs",
        "legal_stringency",
        "human_review_required",
        "nearest_hv_ons_bus_km",
        "nearest_ons_line_km",
        "nearest_idc_km",
        "nearby_renewable_mw",
        "state_curtailed_mwh",
    ]
    top_recommendations = (
        recommendations[top_cols].round(4).to_dict(orient="records")
        if not recommendations.empty
        else []
    )
    summary = {
        "case_study": "sovereign_compute_nexus_phase4_constrained_optimization",
        "method_note": (
            "Discrete H3 constrained multi-objective optimizer. Phase 3 feasibility and "
            "Sentinel-derived Phase 2 degradation risk are enforced as hard constraints before Pareto sorting."
        ),
        "phase3_source": str(PHASE3_GEOJSON),
        "h3_cells": int(len(gdf)),
        "phase4_feasible_cells": int(len(candidates)),
        "pareto_frontier_cells": int(len(frontier)),
        "recommended_sites": int(len(recommendations)),
        "human_review_frontier_cells": int(frontier["human_review_required"].sum()) if not frontier.empty else 0,
        "human_review_recommended_sites": int(recommendations["human_review_required"].sum()) if not recommendations.empty else 0,
        "constraints": {
            "p_deg_rs_epsilon": args.epsilon,
            "max_hv_bus_km": args.max_hv_bus_km,
            "max_line_km": args.max_line_km,
            "max_idc_km": args.max_idc_km,
            "min_renewable_mw": args.min_renewable_mw,
            "exclude_human_review": bool(args.exclude_human_review),
            "fsor_allowed_phase3": "hard constraint",
            "policy_hard_exclusion": "hard constraint",
        },
        "objectives_minimized": OBJECTIVE_COLS,
        "exclusion_reason_counts": {str(k): int(v) for k, v in reason_counts.items()},
        "top_recommendations": top_recommendations,
        "outputs": {
            "all_scored_csv": str(all_csv),
            "all_scored_geojson": str(all_geojson),
            "feasible_candidates_csv": str(candidates_csv),
            "pareto_frontier_csv": str(frontier_csv),
            "pareto_frontier_geojson": str(frontier_geojson) if frontier_geojson.exists() else None,
            "recommended_sites_csv": str(recommended_csv),
            "interactive_map": map_path,
            "charts": chart_paths,
            "summary_json": str(summary_path),
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SCN Phase 4 constrained optimization over Phase 2/3 H3 cells.")
    parser.add_argument("--epsilon", type=float, default=0.62, help="Hard limit for Sentinel degradation probability P_deg(x).")
    parser.add_argument("--max-hv-bus-km", type=float, default=25.0, help="Maximum distance to a high-voltage ONS bus.")
    parser.add_argument("--max-line-km", type=float, default=15.0, help="Maximum distance to an ONS branch.")
    parser.add_argument("--max-idc-km", type=float, default=50.0, help="Maximum distance to an IDC/fiber anchor point.")
    parser.add_argument("--min-renewable-mw", type=float, default=0.0, help="Minimum nearby renewable generation MW.")
    parser.add_argument("--recommendation-count", type=int, default=25, help="Number of final frontier sites to shortlist.")
    parser.add_argument("--exclude-human-review", action="store_true", help="Treat human-review cells as hard exclusions.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    gdf = load_phase3()
    gdf = add_phase4_objectives(gdf)
    gdf, candidates = apply_hard_constraints(gdf, args)
    frontier = pareto_frontier(candidates, OBJECTIVE_COLS)
    recommendations = recommended_sites(frontier, args.recommendation_count)
    if not frontier.empty:
        frontier["phase4_decision_label"] = "pareto_frontier"
        frontier.loc[frontier["human_review_required"].astype(bool), "phase4_decision_label"] = "pareto_frontier_human_review"
    if not candidates.empty:
        candidates["phase4_decision_label"] = "feasible_candidate"
    gdf["phase4_decision_label"] = np.where(gdf["phase4_feasible"], "feasible_candidate", "excluded_by_hard_constraint")
    gdf.loc[gdf["h3_id"].isin(frontier["h3_id"]), "phase4_decision_label"] = "pareto_frontier"
    gdf.loc[gdf["h3_id"].isin(recommendations["h3_id"]), "phase4_decision_label"] = "recommended_shortlist"
    candidates = candidates.merge(gdf[["h3_id", "phase4_decision_label"]], on="h3_id", how="left", suffixes=("", "_final"))
    candidates["phase4_decision_label"] = candidates["phase4_decision_label_final"].fillna(candidates["phase4_decision_label"])
    candidates = candidates.drop(columns=["phase4_decision_label_final"], errors="ignore")
    frontier = frontier.merge(gdf[["h3_id", "phase4_decision_label"]], on="h3_id", how="left", suffixes=("", "_final"))
    frontier["phase4_decision_label"] = frontier["phase4_decision_label_final"].fillna(frontier["phase4_decision_label"])
    frontier = frontier.drop(columns=["phase4_decision_label_final"], errors="ignore")

    summary = write_outputs(gdf, candidates, frontier, recommendations, args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if recommendations.empty:
        print("\nNo Phase 4 recommendations survived. Relax a hard constraint or disable --exclude-human-review.")
        return
    preview_cols = [
        "phase4_rank",
        "h3_id",
        "phase4_resilience_score",
        "p_deg_rs",
        "legal_stringency",
        "human_review_required",
        "nearest_hv_ons_bus_km",
        "nearest_ons_line_km",
        "nearby_renewable_mw",
    ]
    print("\nTop Phase 4 recommended H3 cells")
    print(recommendations[preview_cols].to_string(index=False))


if __name__ == "__main__":
    main()
