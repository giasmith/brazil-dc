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
BASELINE_GEOJSON = SCN_DIR / "phase1_h3_baseline.geojson"
BASELINE_SUMMARY = SCN_DIR / "phase1_h3_baseline_summary.json"
OUT_DIR = SCN_DIR / "mvp_pareto"
CHART_DIR = OUT_DIR / "charts"


LULC_VULNERABILITY = {
    0: 0.10,   # No data / outside raster footprint. Hard exclusions handle offshore cells.
    3: 0.75,   # Forest formation
    4: 0.65,   # Savanna formation
    5: 0.90,   # Mangrove
    9: 0.45,   # Forest plantation
    11: 0.85,  # Wetland
    12: 0.55,  # Grassland
    15: 0.30,  # Pasture
    20: 0.35,  # Sugar cane
    21: 0.40,  # Mosaic agriculture/pasture
    23: 0.25,  # Beach/dune/sand
    24: 0.10,  # Urban area
    25: 0.25,  # Other non-vegetated area
    26: 0.95,  # Water
    29: 0.25,  # Rocky outcrop
    30: 0.20,  # Mining
    31: 0.95,  # Aquaculture
    32: 0.40,  # Salt flat
    33: 0.95,  # River/lake/ocean
    39: 0.35,  # Soybean
    40: 0.40,  # Rice
    41: 0.35,  # Other temporary crops
    46: 0.40,  # Coffee
    47: 0.40,  # Citrus
    48: 0.40,  # Other perennial crops
    49: 0.75,  # Wooded sandbank
    50: 0.70,  # Herbaceous sandbank
    62: 0.35,  # Cotton
    75: 0.15,  # Photovoltaic power plant
}


def normalize_minimize(series: pd.Series, cap_quantile: float = 0.95) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() == 0:
        return pd.Series(0.0, index=series.index)
    fill = values.quantile(cap_quantile)
    values = values.fillna(fill)
    lo = float(values.min())
    hi = float(values.quantile(cap_quantile))
    if math.isclose(lo, hi):
        return pd.Series(0.0, index=series.index)
    return ((values.clip(upper=hi) - lo) / (hi - lo)).clip(0, 1)


def normalize_maximize(series: pd.Series, cap_quantile: float = 0.95) -> pd.Series:
    return 1 - normalize_minimize(series, cap_quantile=cap_quantile)


def deterministic_noise(keys: pd.Series, seed: int) -> pd.Series:
    hashed = pd.util.hash_pandas_object(keys.astype(str) + f"::{seed}", index=False).astype("uint64")
    return (hashed % 10_000).astype(float) / 10_000


def load_baseline() -> gpd.GeoDataFrame:
    if not BASELINE_GEOJSON.exists():
        raise SystemExit(
            "Missing Phase 1 baseline. Run this first:\n"
            "  python3 scripts/build_phase1_h3_baseline.py"
        )
    gdf = gpd.read_file(BASELINE_GEOJSON).to_crs("EPSG:4326")
    bool_cols = [
        "in_case_study_box",
        "protected_overlap",
        "indigenous_overlap",
        "hard_exclusion",
        "outside_state_boundary",
    ]
    for col in bool_cols:
        if col in gdf.columns:
            gdf[col] = gdf[col].astype(bool)
    return gdf


def add_synthetic_degradation(gdf: gpd.GeoDataFrame, seed: int) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    lulc = pd.to_numeric(gdf["dominant_lulc_class"], errors="coerce").fillna(0).astype(int)
    gdf["lulc_vulnerability"] = lulc.map(LULC_VULNERABILITY).fillna(0.50)

    line_pressure = 1 - normalize_minimize(gdf["nearest_ons_line_km"])
    idc_pressure = 1 - normalize_minimize(gdf["nearest_idc_km"])
    bus_pressure = 1 - normalize_minimize(gdf["nearest_hv_ons_bus_km"])
    infrastructure_pressure = (0.45 * line_pressure) + (0.35 * idc_pressure) + (0.20 * bus_pressure)
    noise = deterministic_noise(gdf["h3_id"], seed)

    gdf["infrastructure_pressure"] = infrastructure_pressure.clip(0, 1)
    gdf["synthetic_p_deg"] = (
        0.62 * gdf["lulc_vulnerability"]
        + 0.23 * gdf["infrastructure_pressure"]
        + 0.10 * pd.to_numeric(gdf["water_surface_share_proxy"], errors="coerce").fillna(0).clip(0, 1)
        + 0.05 * noise
    ).clip(0, 1)
    gdf["synthetic_p_deg_note"] = "Mock Phase 2 output for MVP only; replace with trained degradation model."
    return gdf


def add_objectives(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    grid_distance = 0.55 * normalize_minimize(gdf["nearest_hv_ons_bus_km"]) + 0.45 * normalize_minimize(gdf["nearest_ons_line_km"])
    latency = normalize_minimize(gdf["nearest_idc_km"])
    renewable_shortfall = normalize_maximize(gdf["nearby_renewable_mw"])
    curtailment_opportunity_shortfall = normalize_maximize(gdf["state_curtailed_mwh"])
    water_land_risk = (
        0.45 * pd.to_numeric(gdf["synthetic_p_deg"], errors="coerce").fillna(1)
        + 0.35 * pd.to_numeric(gdf["water_surface_share_proxy"], errors="coerce").fillna(0).clip(0, 1)
        + 0.20 * pd.to_numeric(gdf["lulc_vulnerability"], errors="coerce").fillna(0.5)
    ).clip(0, 1)

    gdf["objective_grid_cost"] = grid_distance.clip(0, 1)
    gdf["objective_latency_cost"] = latency.clip(0, 1)
    gdf["objective_renewable_shortfall"] = renewable_shortfall.clip(0, 1)
    gdf["objective_curtailment_shortfall"] = curtailment_opportunity_shortfall.clip(0, 1)
    gdf["objective_water_land_risk"] = water_land_risk
    gdf["composite_mvp_score"] = (
        100
        * (
            0.30 * (1 - gdf["objective_grid_cost"])
            + 0.20 * (1 - gdf["objective_latency_cost"])
            + 0.20 * (1 - gdf["objective_water_land_risk"])
            + 0.20 * (1 - gdf["objective_renewable_shortfall"])
            + 0.10 * (1 - gdf["objective_curtailment_shortfall"])
        )
    ).round(2)
    return gdf


def classify_policy(gdf: gpd.GeoDataFrame, epsilon: float) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    water_class = pd.to_numeric(gdf["dominant_lulc_class"], errors="coerce").isin([26, 31, 33])
    water_surface = pd.to_numeric(gdf["water_surface_share_proxy"], errors="coerce").fillna(0) >= 0.5
    gdf["mvp_hard_exclusion"] = (
        gdf["hard_exclusion"].astype(bool)
        | gdf["outside_state_boundary"].astype(bool)
        | gdf["protected_overlap"].astype(bool)
        | gdf["indigenous_overlap"].astype(bool)
        | water_class
        | water_surface
    )

    gdf["policy_stringency"] = np.select(
        [
            gdf["mvp_hard_exclusion"],
            pd.to_numeric(gdf["synthetic_p_deg"], errors="coerce").fillna(1) > epsilon,
            pd.to_numeric(gdf["lulc_vulnerability"], errors="coerce").fillna(0.5) >= 0.70,
        ],
        ["Critical", "Medium", "Medium"],
        default="Low",
    )
    gdf["lambda_policy"] = gdf["policy_stringency"].map({"Low": 1, "Medium": 5, "Critical": 100}).astype(int)
    return gdf


def feasible_candidates(gdf: gpd.GeoDataFrame, args: argparse.Namespace) -> gpd.GeoDataFrame:
    mask = (
        ~gdf["mvp_hard_exclusion"].astype(bool)
        & (pd.to_numeric(gdf["synthetic_p_deg"], errors="coerce").fillna(1) <= args.epsilon)
        & (pd.to_numeric(gdf["nearest_hv_ons_bus_km"], errors="coerce").fillna(np.inf) <= args.max_hv_bus_km)
        & (pd.to_numeric(gdf["nearest_ons_line_km"], errors="coerce").fillna(np.inf) <= args.max_line_km)
        & (pd.to_numeric(gdf["nearby_renewable_mw"], errors="coerce").fillna(0) >= args.min_renewable_mw)
    )
    return gdf.loc[mask].copy()


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
    return frontier.sort_values("composite_mvp_score", ascending=False)


def write_charts(candidates: gpd.GeoDataFrame, frontier: gpd.GeoDataFrame) -> dict[str, str]:
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    if candidates.empty:
        return paths

    plt.figure(figsize=(9, 6))
    plt.scatter(
        candidates["objective_grid_cost"],
        candidates["objective_latency_cost"],
        c=candidates["synthetic_p_deg"],
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
            s=38,
            edgecolor="white",
            linewidth=0.5,
            label="Pareto frontier",
        )
    plt.xlabel("Grid interconnection cost, normalized")
    plt.ylabel("IDC latency/proximity cost, normalized")
    plt.title("MVP Pareto Frontier: Grid vs. Latency")
    plt.colorbar(label="Synthetic degradation probability")
    plt.legend(loc="best")
    plt.tight_layout()
    path = CHART_DIR / "mvp_frontier_grid_vs_latency.png"
    plt.savefig(path, dpi=180)
    plt.close()
    paths["grid_vs_latency"] = str(path)

    plt.figure(figsize=(9, 6))
    renewable_score = 1 - candidates["objective_renewable_shortfall"]
    plt.scatter(
        renewable_score,
        candidates["objective_water_land_risk"],
        c=candidates["composite_mvp_score"],
        cmap="plasma",
        s=18,
        alpha=0.55,
        label="Feasible H3 cells",
    )
    if not frontier.empty:
        plt.scatter(
            1 - frontier["objective_renewable_shortfall"],
            frontier["objective_water_land_risk"],
            c="black",
            s=38,
            edgecolor="white",
            linewidth=0.5,
            label="Pareto frontier",
        )
    plt.xlabel("Renewable opportunity score, normalized")
    plt.ylabel("Water-land risk, normalized")
    plt.title("MVP Pareto Frontier: Renewables vs. Water-Land Risk")
    plt.colorbar(label="Composite MVP score")
    plt.legend(loc="best")
    plt.tight_layout()
    path = CHART_DIR / "mvp_frontier_renewables_vs_risk.png"
    plt.savefig(path, dpi=180)
    plt.close()
    paths["renewables_vs_risk"] = str(path)
    return paths


def write_map(gdf: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame, frontier: gpd.GeoDataFrame) -> str | None:
    try:
        import folium
        from branca.colormap import LinearColormap
    except ModuleNotFoundError:
        return None

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    center = [float(gdf["center_lat"].median()), float(gdf["center_lon"].median())]
    fmap = folium.Map(location=center, zoom_start=10, tiles="CartoDB positron", control_scale=True)
    cmap = LinearColormap(["#f7fcf0", "#addd8e", "#31a354", "#006837"], vmin=0, vmax=100, caption="Composite MVP score")

    def style_all(feature):
        props = feature["properties"]
        if props.get("mvp_hard_exclusion"):
            return {"fillColor": "#bdbdbd", "color": "#969696", "weight": 0.2, "fillOpacity": 0.18}
        return {"fillColor": "#e5f5f9", "color": "#9ecae1", "weight": 0.2, "fillOpacity": 0.12}

    popup_fields = [
        "h3_id",
        "state_code",
        "dominant_lulc_class",
        "synthetic_p_deg",
        "policy_stringency",
        "nearest_hv_ons_bus_km",
        "nearest_ons_line_km",
        "nearest_idc_km",
        "nearby_renewable_mw",
        "composite_mvp_score",
    ]

    inspector_layers = []
    all_layer = folium.GeoJson(
        gdf[
            [
                "h3_id",
                "state_code",
                "dominant_lulc_class",
                "synthetic_p_deg",
                "policy_stringency",
                "mvp_hard_exclusion",
                "geometry",
            ]
        ],
        name="All H3 cells and hard exclusions",
        style_function=style_all,
        tooltip=folium.GeoJsonTooltip(fields=["h3_id", "policy_stringency", "synthetic_p_deg"]),
    ).add_to(fmap)
    inspector_layers.append(all_layer)

    if not candidates.empty:
        candidate_layer = folium.GeoJson(
            candidates[popup_fields + ["geometry"]],
            name="Feasible candidates",
            style_function=lambda feature: {
                "fillColor": cmap(float(feature["properties"].get("composite_mvp_score", 0))),
                "color": "#2b8cbe",
                "weight": 0.4,
                "fillOpacity": 0.42,
            },
            tooltip=folium.GeoJsonTooltip(fields=["h3_id", "composite_mvp_score", "synthetic_p_deg"]),
        ).add_to(fmap)
        inspector_layers.append(candidate_layer)

    if not frontier.empty:
        frontier_layer = folium.GeoJson(
            frontier[popup_fields + ["geometry"]],
            name="Pareto frontier",
            style_function=lambda feature: {
                "fillColor": "#d7301f",
                "color": "#7f0000",
                "weight": 1.8,
                "fillOpacity": 0.72,
            },
            tooltip=folium.GeoJsonTooltip(fields=["h3_id", "composite_mvp_score", "synthetic_p_deg"]),
        ).add_to(fmap)
        inspector_layers.append(frontier_layer)

    add_bottom_detail_panel(
        fmap,
        inspector_layers,
        title="MVP Pareto Frontier Inspector",
        subtitle=(
            "Click an H3 cell to inspect feasibility, synthetic degradation risk, renewable opportunity, "
            "grid proximity, and the composite Pareto screening score."
        ),
        metric_fields=[
            "composite_mvp_score",
            "synthetic_p_deg",
            "policy_stringency",
            "mvp_hard_exclusion",
            "nearby_renewable_mw",
            "nearest_hv_ons_bus_km",
        ],
        detail_fields=popup_fields + ["mvp_hard_exclusion"],
        label_map={
            "h3_id": "H3 ID",
            "state_code": "State",
            "dominant_lulc_class": "LULC Class",
            "synthetic_p_deg": "Synthetic P_deg(x)",
            "policy_stringency": "Policy Stringency",
            "mvp_hard_exclusion": "Hard Exclusion",
            "nearest_hv_ons_bus_km": "Nearest HV Bus",
            "nearest_ons_line_km": "Nearest ONS Line",
            "nearest_idc_km": "Nearest IDC",
            "nearby_renewable_mw": "Nearby Renewable Capacity",
            "composite_mvp_score": "Composite MVP Score",
        },
        panel_id="mvp-pareto-inspector",
    )
    cmap.add_to(fmap)
    folium.LayerControl(collapsed=False).add_to(fmap)
    map_path = OUT_DIR / "mvp_pareto_frontier_map.html"
    fmap.save(map_path)
    return str(map_path)


def write_outputs(gdf: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame, frontier: gpd.GeoDataFrame, args: argparse.Namespace) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gdf_out = OUT_DIR / "mvp_all_h3_scored.geojson"
    candidates_csv = OUT_DIR / "mvp_feasible_candidates.csv"
    frontier_csv = OUT_DIR / "mvp_pareto_frontier.csv"
    frontier_geojson = OUT_DIR / "mvp_pareto_frontier.geojson"
    summary_path = OUT_DIR / "mvp_pareto_summary.json"

    gdf.to_file(gdf_out, driver="GeoJSON")
    candidates.drop(columns="geometry", errors="ignore").sort_values("composite_mvp_score", ascending=False).to_csv(candidates_csv, index=False)
    frontier.drop(columns="geometry", errors="ignore").to_csv(frontier_csv, index=False)
    if not frontier.empty:
        frontier.to_file(frontier_geojson, driver="GeoJSON")
    elif frontier_geojson.exists():
        frontier_geojson.unlink()

    chart_paths = write_charts(candidates, frontier)
    map_path = write_map(gdf, candidates, frontier)

    baseline_summary = {}
    if BASELINE_SUMMARY.exists():
        baseline_summary = json.loads(BASELINE_SUMMARY.read_text())

    top_cols = [
        "h3_id",
        "center_lat",
        "center_lon",
        "synthetic_p_deg",
        "composite_mvp_score",
        "nearest_hv_ons_bus_km",
        "nearest_ons_line_km",
        "nearest_idc_km",
        "nearby_renewable_mw",
        "dominant_lulc_class",
    ]
    top_candidates = (
        frontier[top_cols].head(10).round(4).to_dict(orient="records")
        if not frontier.empty
        else []
    )
    summary = {
        "case_study": "sovereign_compute_nexus_48h_mvp",
        "method_note": (
            "This is a toy proof of the pipeline logic. synthetic_p_deg is a deterministic mock Phase 2 output, "
            "not a trained remote-sensing model."
        ),
        "phase1_source": str(BASELINE_GEOJSON),
        "baseline_h3_cells": int(len(gdf)),
        "baseline_hard_exclusion_cells": int(gdf["hard_exclusion"].sum()),
        "mvp_hard_exclusion_cells": int(gdf["mvp_hard_exclusion"].sum()),
        "feasible_candidate_cells": int(len(candidates)),
        "pareto_frontier_cells": int(len(frontier)),
        "constraints": {
            "synthetic_p_deg_epsilon": args.epsilon,
            "max_hv_bus_km": args.max_hv_bus_km,
            "max_line_km": args.max_line_km,
            "min_renewable_mw": args.min_renewable_mw,
            "protected_overlap": "hard exclusion",
            "indigenous_overlap": "hard exclusion",
            "outside_state_boundary": "hard exclusion",
            "surface_water_or_water_lulc": "hard exclusion",
        },
        "objectives_minimized": [
            "objective_grid_cost",
            "objective_latency_cost",
            "objective_water_land_risk",
            "objective_renewable_shortfall",
            "objective_curtailment_shortfall",
        ],
        "top_frontier_candidates": top_candidates,
        "outputs": {
            "all_scored_geojson": str(gdf_out),
            "feasible_candidates_csv": str(candidates_csv),
            "pareto_frontier_csv": str(frontier_csv),
            "pareto_frontier_geojson": str(frontier_geojson) if frontier_geojson.exists() else None,
            "interactive_map": map_path,
            "charts": chart_paths,
        },
        "phase1_summary": baseline_summary,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the 48-hour MVP Pareto siting model for the Sovereign Compute Nexus.")
    parser.add_argument("--epsilon", type=float, default=0.62, help="Hard limit for synthetic degradation probability P_deg(x).")
    parser.add_argument("--max-hv-bus-km", type=float, default=25.0, help="Maximum distance to a >=230 kV ONS bus.")
    parser.add_argument("--max-line-km", type=float, default=15.0, help="Maximum distance to an ONS branch.")
    parser.add_argument("--min-renewable-mw", type=float, default=0.0, help="Minimum nearby renewable generation MW.")
    parser.add_argument("--seed", type=int, default=42, help="Seed for deterministic mock ML texture.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    gdf = load_baseline()
    gdf = add_synthetic_degradation(gdf, args.seed)
    gdf = add_objectives(gdf)
    gdf = classify_policy(gdf, args.epsilon)
    candidates = feasible_candidates(gdf, args)
    objective_cols = [
        "objective_grid_cost",
        "objective_latency_cost",
        "objective_water_land_risk",
        "objective_renewable_shortfall",
        "objective_curtailment_shortfall",
    ]
    frontier = pareto_frontier(candidates, objective_cols)
    summary = write_outputs(gdf, candidates, frontier, args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if frontier.empty:
        print("\nNo Pareto candidates survived. Try relaxing --epsilon, --max-hv-bus-km, or --max-line-km.")
        return

    preview_cols = [
        "h3_id",
        "synthetic_p_deg",
        "composite_mvp_score",
        "nearest_hv_ons_bus_km",
        "nearest_ons_line_km",
        "nearest_idc_km",
        "nearby_renewable_mw",
        "dominant_lulc_class",
    ]
    print("\nTop frontier candidates")
    print(frontier[preview_cols].head(15).to_string(index=False))


if __name__ == "__main__":
    main()
