from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

from map_ui import add_bottom_detail_panel


ROOT = Path(__file__).resolve().parents[1]
SCN_DIR = ROOT / "clean_data" / "sovereign_compute_nexus"
OUT_DIR = SCN_DIR / "phase2_rs"
BASELINE_GEOJSON = SCN_DIR / "phase1_h3_baseline.geojson"
TERRITORIAL_GPKG = ROOT / "clean_data" / "brazil_territorial_layers.gpkg"
LULC_LEGEND = ROOT / "clean_data" / "lulc_water" / "lulc_class_legend.csv"
DEFAULT_SENTINEL_FEATURES = ROOT / "phase2_rs_features_for_solver.csv"
PROJECTED_CRS = "EPSG:3857"


LULC_VULNERABILITY = {
    0: 0.10,
    3: 0.75,
    4: 0.65,
    5: 0.90,
    6: 0.90,
    9: 0.45,
    11: 0.85,
    12: 0.55,
    15: 0.30,
    20: 0.35,
    21: 0.40,
    23: 0.25,
    24: 0.10,
    25: 0.25,
    26: 0.95,
    29: 0.25,
    30: 0.20,
    31: 0.95,
    32: 0.40,
    33: 0.95,
    39: 0.35,
    40: 0.40,
    41: 0.35,
    46: 0.40,
    47: 0.40,
    48: 0.40,
    49: 0.75,
    50: 0.70,
    62: 0.35,
    75: 0.15,
}


def require_h3():
    try:
        import h3  # type: ignore

        return h3
    except ModuleNotFoundError as exc:
        raise SystemExit("Missing h3. Install with: python3 -m pip install h3") from exc


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


def normalize_score(series: pd.Series, cap_quantile: float = 0.95) -> pd.Series:
    return 1 - normalize_minimize(series, cap_quantile=cap_quantile)


def load_baseline() -> gpd.GeoDataFrame:
    if not BASELINE_GEOJSON.exists():
        raise SystemExit("Missing Phase 1 H3 baseline. Run scripts/build_phase1_h3_baseline.py first.")
    gdf = gpd.read_file(BASELINE_GEOJSON).to_crs("EPSG:4326")
    for col in ["protected_overlap", "indigenous_overlap", "hard_exclusion", "outside_state_boundary"]:
        gdf[col] = gdf[col].astype(bool)
    return gdf


def add_lulc_labels(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    gdf["dominant_lulc_class"] = pd.to_numeric(gdf["dominant_lulc_class"], errors="coerce").fillna(0).astype(int)
    gdf["rs_lulc_vulnerability"] = gdf["dominant_lulc_class"].map(LULC_VULNERABILITY).fillna(0.50)
    if LULC_LEGEND.exists():
        legend = pd.read_csv(LULC_LEGEND)
        gdf = gdf.merge(
            legend.rename(columns={"class_code": "dominant_lulc_class"}),
            on="dominant_lulc_class",
            how="left",
        )
    else:
        gdf["class_name"] = "Unknown"
        gdf["class_group"] = "unknown"
    return gdf


def h3_neighbor_ids(cell: str) -> set[str]:
    h3 = require_h3()
    if hasattr(h3, "grid_disk"):
        return set(h3.grid_disk(cell, 1))
    return set(h3.k_ring(cell, 1))


def add_fray_flags(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    constrained_ids = set(gdf.loc[gdf["protected_overlap"] | gdf["indigenous_overlap"], "h3_id"])
    protected_ids = set(gdf.loc[gdf["protected_overlap"], "h3_id"])
    indigenous_ids = set(gdf.loc[gdf["indigenous_overlap"], "h3_id"])

    protected_fray = []
    indigenous_fray = []
    any_fray = []
    for cell in gdf["h3_id"]:
        neighbors = h3_neighbor_ids(cell)
        protected_hit = bool((neighbors & protected_ids) and cell not in protected_ids)
        indigenous_hit = bool((neighbors & indigenous_ids) and cell not in indigenous_ids)
        protected_fray.append(protected_hit)
        indigenous_fray.append(indigenous_hit)
        any_fray.append(bool((neighbors & constrained_ids) and cell not in constrained_ids))
    gdf["protected_fray_cell"] = protected_fray
    gdf["indigenous_fray_cell"] = indigenous_fray
    gdf["boundary_fray_cell"] = any_fray
    return gdf


def nearest_constraint_context(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    layers = gpd.read_file(TERRITORIAL_GPKG, layer="all_layers").to_crs("EPSG:4326")
    constraints = layers[layers["source_layer"].isin(["protected_lands", "indigenous_lands"])].copy()
    constraints = constraints[constraints.geometry.notna() & ~constraints.geometry.is_empty].copy()
    if constraints.empty:
        gdf["nearest_constraint_km"] = np.nan
        gdf["nearest_constraint_layer"] = None
        gdf["nearest_constraint_name"] = None
        return gdf

    left = gdf[["h3_id", "geometry"]].copy()
    left["geometry"] = left.geometry.representative_point()
    left = left.to_crs(PROJECTED_CRS)
    right_cols = ["source_layer", "feature_name", "fase_ti", "categoria", "grupo", "geometry"]
    right = constraints[right_cols].to_crs(PROJECTED_CRS)
    joined = gpd.sjoin_nearest(left, right, how="left", distance_col="nearest_constraint_m")
    joined = joined.sort_values(["h3_id", "nearest_constraint_m"]).drop_duplicates("h3_id")
    joined["nearest_constraint_km"] = joined["nearest_constraint_m"] / 1000
    attrs = joined[
        [
            "h3_id",
            "nearest_constraint_km",
            "source_layer",
            "feature_name",
            "fase_ti",
            "categoria",
            "grupo",
        ]
    ].rename(
        columns={
            "source_layer": "nearest_constraint_layer",
            "feature_name": "nearest_constraint_name",
            "fase_ti": "nearest_indigenous_phase",
            "categoria": "nearest_protected_category",
            "grupo": "nearest_protected_group",
        }
    )
    return gdf.merge(attrs, on="h3_id", how="left")


def add_rs_feature_proxies(gdf: gpd.GeoDataFrame, edge_decay_km: float) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    line_pressure = 1 - normalize_minimize(gdf["nearest_ons_line_km"])
    idc_pressure = 1 - normalize_minimize(gdf["nearest_idc_km"])
    bus_pressure = 1 - normalize_minimize(gdf["nearest_hv_ons_bus_km"])
    gdf["rs_infrastructure_pressure"] = (0.45 * line_pressure + 0.35 * idc_pressure + 0.20 * bus_pressure).clip(0, 1)

    distance = pd.to_numeric(gdf["nearest_constraint_km"], errors="coerce").fillna(edge_decay_km * 4)
    edge_exposure = np.exp(-distance / edge_decay_km)
    edge_exposure = pd.Series(edge_exposure, index=gdf.index).clip(0, 1)
    edge_exposure = edge_exposure.mask(gdf["boundary_fray_cell"], np.maximum(edge_exposure, 0.80))
    edge_exposure = edge_exposure.mask(gdf["protected_overlap"] | gdf["indigenous_overlap"], 1.0)
    gdf["rs_edge_exposure"] = edge_exposure

    water_surface = pd.to_numeric(gdf["water_surface_share_proxy"], errors="coerce").fillna(0).clip(0, 1)
    gdf["rs_surface_water_sensitivity"] = np.where(
        gdf["dominant_lulc_class"].isin([11, 26, 31, 33]),
        1.0,
        0.35 * water_surface + 0.65 * gdf["rs_lulc_vulnerability"],
    )
    gdf["rs_nearby_renewable_score"] = normalize_score(gdf["nearby_renewable_mw"])
    gdf["rs_transmission_access_score"] = 1 - (
        0.55 * normalize_minimize(gdf["nearest_hv_ons_bus_km"]) + 0.45 * normalize_minimize(gdf["nearest_ons_line_km"])
    )
    return gdf


def add_pca_autoencoder_proxy(gdf: gpd.GeoDataFrame, pca_components: int) -> tuple[gpd.GeoDataFrame, dict]:
    gdf = gdf.copy()
    feature_cols = [
        "rs_lulc_vulnerability",
        "rs_infrastructure_pressure",
        "rs_edge_exposure",
        "rs_surface_water_sensitivity",
        "rs_nearby_renewable_score",
        "rs_transmission_access_score",
    ]
    X = gdf[feature_cols].astype(float).fillna(0)
    train_mask = (
        ~gdf["hard_exclusion"].astype(bool)
        & ~gdf["boundary_fray_cell"].astype(bool)
        & (pd.to_numeric(gdf["water_surface_share_proxy"], errors="coerce").fillna(0) < 0.5)
    )
    if int(train_mask.sum()) < 25:
        train_mask = ~gdf["hard_exclusion"].astype(bool)
    if int(train_mask.sum()) < 10:
        train_mask = pd.Series(True, index=gdf.index)

    scaler = StandardScaler()
    scaler.fit(X.loc[train_mask])
    X_scaled = scaler.transform(X)
    n_components = max(1, min(pca_components, X.shape[1] - 1, int(train_mask.sum()) - 1))
    pca = PCA(n_components=n_components, random_state=42)
    pca.fit(scaler.transform(X.loc[train_mask]))
    reconstructed = pca.inverse_transform(pca.transform(X_scaled))
    reconstruction_error = ((X_scaled - reconstructed) ** 2).mean(axis=1)
    gdf["rs_reconstruction_error"] = reconstruction_error
    gdf["rs_reconstruction_error_norm"] = normalize_minimize(pd.Series(reconstruction_error, index=gdf.index), cap_quantile=0.98)
    meta = {
        "model": "PCA reconstruction-error proxy",
        "note": "Temporary RS Phase 2 proxy until Sentinel-1/2 tensors are available for a convolutional autoencoder.",
        "feature_columns": feature_cols,
        "training_cells": int(train_mask.sum()),
        "pca_components": int(n_components),
        "explained_variance_ratio": [float(v) for v in pca.explained_variance_ratio_],
    }
    return gdf, meta


def add_sentinel_features(gdf: gpd.GeoDataFrame, sentinel_features: Path | None) -> tuple[gpd.GeoDataFrame, dict]:
    if sentinel_features is None or not sentinel_features.exists():
        return gdf, {
            "enabled": False,
            "source": str(sentinel_features) if sentinel_features else None,
            "note": "No Sentinel H3 feature table was provided; using local proxy features only.",
        }

    sentinel = pd.read_csv(sentinel_features)
    if "h3_id" not in sentinel.columns:
        raise SystemExit(f"Sentinel feature table is missing h3_id: {sentinel_features}")

    sentinel_cols = [
        col
        for col in sentinel.columns
        if col.startswith(("ndvi_s2", "ndwi_s2", "vv_s1", "vh_s1"))
    ]
    if not sentinel_cols:
        raise SystemExit(f"Sentinel feature table has no ndvi/ndwi/vv/vh columns: {sentinel_features}")

    sentinel = sentinel[["h3_id"] + sentinel_cols].drop_duplicates("h3_id")
    gdf = gdf.drop(columns=[col for col in sentinel_cols if col in gdf.columns], errors="ignore")
    gdf = gdf.merge(sentinel, on="h3_id", how="left")
    for col in sentinel_cols:
        gdf[col] = pd.to_numeric(gdf[col], errors="coerce")

    ndvi_cols = [col for col in sentinel_cols if col.startswith("ndvi_s2")]
    ndwi_cols = [col for col in sentinel_cols if col.startswith("ndwi_s2")]
    vv_cols = [col for col in sentinel_cols if col.startswith("vv_s1")]
    vh_cols = [col for col in sentinel_cols if col.startswith("vh_s1")]

    if "ndvi_s2_median" in gdf.columns:
        ndvi = gdf["ndvi_s2_median"]
    else:
        ndvi = gdf[ndvi_cols].median(axis=1) if ndvi_cols else pd.Series(np.nan, index=gdf.index)

    if "ndwi_s2_median" in gdf.columns:
        ndwi = gdf["ndwi_s2_median"]
    else:
        ndwi = gdf[ndwi_cols].median(axis=1) if ndwi_cols else pd.Series(np.nan, index=gdf.index)

    vv = gdf[vv_cols].median(axis=1) if vv_cols else pd.Series(np.nan, index=gdf.index)
    vh = gdf[vh_cols].median(axis=1) if vh_cols else pd.Series(np.nan, index=gdf.index)

    gdf["rs_sentinel_ndvi"] = ndvi
    gdf["rs_sentinel_ndwi"] = ndwi
    gdf["rs_sentinel_vv"] = vv
    gdf["rs_sentinel_vh"] = vh

    gdf["rs_sentinel_optical_stress"] = normalize_score(ndvi)
    gdf["rs_sentinel_water_signal"] = normalize_minimize(ndwi)
    vv_pressure = normalize_minimize(vv)
    vh_pressure = normalize_minimize(vh)
    gdf["rs_sentinel_sar_pressure"] = (0.5 * vv_pressure + 0.5 * vh_pressure).clip(0, 1)
    gdf["rs_sentinel_available"] = gdf[["rs_sentinel_ndvi", "rs_sentinel_ndwi", "rs_sentinel_vv", "rs_sentinel_vh"]].notna().any(axis=1)
    gdf["rs_sentinel_signal"] = (
        0.45 * gdf["rs_sentinel_optical_stress"]
        + 0.25 * gdf["rs_sentinel_water_signal"]
        + 0.30 * gdf["rs_sentinel_sar_pressure"]
    ).clip(0, 1)

    return gdf, {
        "enabled": True,
        "source": str(sentinel_features),
        "rows": int(len(sentinel)),
        "matched_h3_cells": int(gdf["rs_sentinel_available"].sum()),
        "feature_columns": sentinel_cols,
        "derived_columns": [
            "rs_sentinel_ndvi",
            "rs_sentinel_ndwi",
            "rs_sentinel_vv",
            "rs_sentinel_vh",
            "rs_sentinel_optical_stress",
            "rs_sentinel_water_signal",
            "rs_sentinel_sar_pressure",
            "rs_sentinel_signal",
        ],
        "method": (
            "Google Drive-mounted Sentinel-1/2 GEE exports were zonally summarized "
            "to H3 resolution 8 cells; NDVI/NDWI and VV/VH means are used as true "
            "remote-sensing edge-effect features."
        ),
    }


def add_alphaearth_features(gdf: gpd.GeoDataFrame, aef_features: Path | None, aef_weight: float) -> tuple[gpd.GeoDataFrame, dict]:
    """Merge per-H3 AlphaEarth change features (ingest_alphaearth_h3.py) and optionally blend them into rs_sentinel_signal.

    Only the cosine-change columns are used as signals; the 64 embedding axes are carried for maps/anomaly work but never scored.
    """
    if aef_features is None or not aef_features.exists():
        return gdf, {"enabled": False, "source": str(aef_features) if aef_features else None}
    if not 0.0 <= aef_weight <= 1.0:
        raise SystemExit("--aef-weight must be between 0 and 1")

    aef = pd.read_parquet(aef_features) if aef_features.suffix == ".parquet" else pd.read_csv(aef_features)
    if "h3_id" not in aef.columns:
        raise SystemExit(f"AlphaEarth feature table is missing h3_id: {aef_features}")
    signal_cols = [c for c in ("aef_change_span", "aef_change_prev_max") if c in aef.columns]
    if not signal_cols:
        raise SystemExit(f"AlphaEarth feature table has no aef_change_* columns: {aef_features}")
    keep = ["h3_id"] + signal_cols + [c for c in aef.columns if c.startswith(("aef_pc", "aef_n_pixels_"))]
    aef = aef[keep].drop_duplicates("h3_id")
    gdf = gdf.drop(columns=[c for c in keep if c != "h3_id" and c in gdf.columns], errors="ignore").merge(aef, on="h3_id", how="left")

    change = pd.to_numeric(gdf["aef_change_span"] if "aef_change_span" in gdf.columns else gdf[signal_cols[0]], errors="coerce")
    gdf["rs_aef_available"] = change.notna()
    gdf["rs_aef_change_signal"] = normalize_minimize(change)  # 0 = stable signature, 1 = most changed (95th pct cap)
    blended = aef_weight > 0  # applied inside add_degradation_probability as its own p_deg term
    return gdf, {
        "enabled": True,
        "source": str(aef_features),
        "rows": int(len(aef)),
        "matched_h3_cells": int(gdf["rs_aef_available"].sum()),
        "unmatched_h3_cells": int((~gdf["rs_aef_available"]).sum()),
        "signal_column": "aef_change_span" if "aef_change_span" in signal_cols else signal_cols[0],
        "aef_weight": aef_weight,
        "used_in_p_deg_rs": blended,
        "method": (
            "Google Satellite Embedding V1 (AlphaEarth) annual 64-d unit vectors were averaged per H3 cell in Earth Engine; "
            "1 - cosine similarity between years is used as a learned-but-bounded change signal. Embedding axes are not scored."
        ),
    }


def add_degradation_probability(gdf: gpd.GeoDataFrame, use_sentinel: bool, aef_weight: float = 0.0) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    water = pd.to_numeric(gdf["water_surface_share_proxy"], errors="coerce").fillna(0).clip(0, 1)
    proxy_p_deg = (
        0.32 * gdf["rs_edge_exposure"]
        + 0.24 * gdf["rs_reconstruction_error_norm"]
        + 0.19 * gdf["rs_lulc_vulnerability"]
        + 0.17 * gdf["rs_infrastructure_pressure"]
        + 0.08 * gdf["rs_surface_water_sensitivity"]
    ).clip(0, 1)
    if use_sentinel and "rs_sentinel_signal" in gdf.columns:
        # AlphaEarth year-over-year change gets its own term when requested; the other terms are
        # scaled by (1 - w) so the weights still sum to 1 and w=0 reproduces the original formula.
        w = aef_weight if (aef_weight > 0 and "rs_aef_change_signal" in gdf.columns) else 0.0
        aef_term = gdf["rs_aef_change_signal"].fillna(0.0) if w else 0.0
        sentinel_p_deg = (
            (1 - w) * (
                0.24 * gdf["rs_edge_exposure"]
                + 0.24 * gdf["rs_sentinel_optical_stress"]
                + 0.16 * gdf["rs_sentinel_water_signal"]
                + 0.14 * gdf["rs_sentinel_sar_pressure"]
                + 0.11 * gdf["rs_lulc_vulnerability"]
                + 0.08 * gdf["rs_infrastructure_pressure"]
                + 0.03 * gdf["rs_reconstruction_error_norm"]
            )
            + w * aef_term
        ).clip(0, 1)
        available = gdf["rs_sentinel_available"].astype(bool)
        p_deg = proxy_p_deg.mask(available, sentinel_p_deg)
        gdf["p_deg_rs_source"] = np.where(
            available,
            "sentinel_h3_zonal_mean_optical_sar",
            "local_mapbiomas_water_h3_pca_proxy_fallback",
        )
        gdf["phase2_status"] = np.where(
            available,
            "sentinel_extracted_ready_for_solver",
            "proxy_fallback_ready_for_solver",
        )
        gdf["phase2_limitations"] = np.where(
            available,
            (
                "Uses Google Drive-mounted GEE Sentinel-2 NDVI/NDWI and Sentinel-1 VV/VH "
                "zonal means by H3 cell; next upgrade is multi-period autoencoder training."
            ),
            (
                "No Sentinel feature matched this H3 cell; retained local MapBiomas/water/"
                "infrastructure PCA proxy fallback."
            ),
        )
    else:
        p_deg = proxy_p_deg
        gdf["p_deg_rs_source"] = "local_mapbiomas_water_h3_pca_proxy"
        gdf["phase2_status"] = "proxy_ready_for_solver"
        gdf["phase2_limitations"] = (
            "Uses local MapBiomas/water/H3/infrastructure proxies; replace with Sentinel-1/2 "
            "NDVI/NDWI/NBR/SAR tensors for publication-grade remote sensing."
        )

    p_deg = p_deg.mask(gdf["protected_overlap"] | gdf["indigenous_overlap"], np.maximum(p_deg, 0.90))
    p_deg = p_deg.mask(water >= 0.5, np.maximum(p_deg, 0.85))
    gdf["p_deg_rs"] = p_deg.clip(0, 1)
    gdf["treatment_proxy_near_infrastructure_fray"] = (
        gdf["boundary_fray_cell"]
        & (
            (pd.to_numeric(gdf["nearest_ons_line_km"], errors="coerce").fillna(np.inf) <= 2.0)
            | (pd.to_numeric(gdf["nearest_hv_ons_bus_km"], errors="coerce").fillna(np.inf) <= 5.0)
            | (pd.to_numeric(gdf["nearest_idc_km"], errors="coerce").fillna(np.inf) <= 5.0)
        )
    )
    gdf["did_ready_status"] = np.where(
        gdf["treatment_proxy_near_infrastructure_fray"],
        "treated_edge_cell_needs_time_series_outcome",
        "control_or_nonedge_cell_needs_time_series_outcome",
    )
    return gdf


def write_map(gdf: gpd.GeoDataFrame) -> str | None:
    try:
        import folium
        from branca.colormap import LinearColormap
    except ModuleNotFoundError:
        return None

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    center = [float(gdf["center_lat"].median()), float(gdf["center_lon"].median())]
    fmap = folium.Map(location=center, zoom_start=10, tiles="CartoDB positron", control_scale=True)
    cmap = LinearColormap(["#f7fcf0", "#c7e9b4", "#7fcdbb", "#2c7fb8", "#253494"], vmin=0, vmax=1, caption="RS Phase 2 P_deg(x)")
    fields = [
        "h3_id",
        "p_deg_rs",
        "p_deg_rs_source",
        "boundary_fray_cell",
        "rs_edge_exposure",
        "rs_reconstruction_error_norm",
        "dominant_lulc_class",
        "class_name",
        "nearest_constraint_km",
        "nearest_constraint_layer",
        "nearest_constraint_name",
        "treatment_proxy_near_infrastructure_fray",
    ]
    for col in [
        "rs_sentinel_ndvi",
        "rs_sentinel_ndwi",
        "rs_sentinel_vv",
        "rs_sentinel_vh",
        "rs_sentinel_signal",
    ]:
        if col in gdf.columns:
            fields.append(col)

    def style(feature):
        props = feature["properties"]
        if props.get("hard_exclusion"):
            return {"fillColor": "#bdbdbd", "color": "#969696", "weight": 0.25, "fillOpacity": 0.20}
        weight = 1.0 if props.get("boundary_fray_cell") else 0.25
        color = "#e31a1c" if props.get("boundary_fray_cell") else "#6baed6"
        return {
            "fillColor": cmap(float(props.get("p_deg_rs", 0))),
            "color": color,
            "weight": weight,
            "fillOpacity": 0.48,
        }

    rs_layer = folium.GeoJson(
        gdf[fields + ["hard_exclusion", "geometry"]],
        name="RS edge-effect P_deg(x)",
        style_function=style,
        tooltip=folium.GeoJsonTooltip(fields=["h3_id", "p_deg_rs", "boundary_fray_cell", "class_name"]),
    ).add_to(fmap)
    add_bottom_detail_panel(
        fmap,
        rs_layer,
        title="Remote-Sensing Edge-Effect Inspector",
        subtitle=(
            "Click an H3 cell to inspect Sentinel-derived degradation risk, protected-land edge exposure, "
            "land cover, and infrastructure pressure."
        ),
        metric_fields=[
            "p_deg_rs",
            "rs_sentinel_signal",
            "rs_sentinel_ndvi",
            "rs_sentinel_ndwi",
            "boundary_fray_cell",
            "nearest_constraint_km",
        ],
        detail_fields=fields,
        label_map={
            "h3_id": "H3 ID",
            "p_deg_rs": "P_deg(x)",
            "p_deg_rs_source": "Risk Source",
            "boundary_fray_cell": "Boundary Fray Cell",
            "rs_edge_exposure": "Edge Exposure",
            "rs_reconstruction_error_norm": "PCA Reconstruction Proxy",
            "dominant_lulc_class": "LULC Class",
            "class_name": "Land-Cover Class",
            "nearest_constraint_km": "Nearest Constraint",
            "nearest_constraint_layer": "Constraint Layer",
            "nearest_constraint_name": "Constraint Name",
            "treatment_proxy_near_infrastructure_fray": "Infrastructure Fray Treatment",
            "rs_sentinel_ndvi": "Sentinel-2 NDVI",
            "rs_sentinel_ndwi": "Sentinel-2 NDWI",
            "rs_sentinel_vv": "Sentinel-1 VV",
            "rs_sentinel_vh": "Sentinel-1 VH",
            "rs_sentinel_signal": "Sentinel Stress Signal",
        },
        panel_id="phase2-rs-inspector",
    )
    cmap.add_to(fmap)
    folium.LayerControl(collapsed=False).add_to(fmap)
    path = OUT_DIR / "phase2_rs_edge_effect_map.html"
    fmap.save(path)
    return str(path)


def write_outputs(gdf: gpd.GeoDataFrame, model_meta: dict, sentinel_meta: dict, args: argparse.Namespace) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "phase2_rs_edge_effects.csv"
    geojson_path = OUT_DIR / "phase2_rs_edge_effects.geojson"
    solver_path = OUT_DIR / "phase2_rs_features_for_solver.csv"
    summary_path = OUT_DIR / "phase2_rs_summary.json"

    gdf.to_file(geojson_path, driver="GeoJSON")
    gdf.drop(columns="geometry", errors="ignore").to_csv(csv_path, index=False)
    solver_cols = [
        "h3_id",
        "p_deg_rs",
        "p_deg_rs_source",
        "phase2_status",
        "boundary_fray_cell",
        "protected_fray_cell",
        "indigenous_fray_cell",
        "rs_edge_exposure",
        "rs_reconstruction_error_norm",
        "rs_lulc_vulnerability",
        "rs_infrastructure_pressure",
        "rs_surface_water_sensitivity",
        "treatment_proxy_near_infrastructure_fray",
        "did_ready_status",
    ]
    solver_cols += [
        col
        for col in [
            "rs_sentinel_ndvi",
            "rs_sentinel_ndwi",
            "rs_sentinel_vv",
            "rs_sentinel_vh",
            "rs_sentinel_optical_stress",
            "rs_sentinel_water_signal",
            "rs_sentinel_sar_pressure",
            "rs_sentinel_signal",
            "rs_sentinel_available",
            "ndvi_s2_median",
            "ndwi_s2_median",
            "vv_s1_median",
            "vh_s1_median",
        ]
        if col in gdf.columns
    ]
    gdf[solver_cols].to_csv(solver_path, index=False)
    map_path = write_map(gdf)

    summary = {
        "case_study": "sovereign_compute_nexus_phase2_rs",
        "method_note": (
            "Phase 2 uses Sentinel-derived H3 zonal features when provided, with the "
            "local PCA proxy retained as fallback and validation scaffold."
        ),
        "baseline_source": str(BASELINE_GEOJSON),
        "sentinel_features": sentinel_meta,
        "h3_cells": int(len(gdf)),
        "boundary_fray_cells": int(gdf["boundary_fray_cell"].sum()),
        "protected_fray_cells": int(gdf["protected_fray_cell"].sum()),
        "indigenous_fray_cells": int(gdf["indigenous_fray_cell"].sum()),
        "treated_edge_proxy_cells": int(gdf["treatment_proxy_near_infrastructure_fray"].sum()),
        "sentinel_matched_h3_cells": int(gdf["rs_sentinel_available"].sum()) if "rs_sentinel_available" in gdf.columns else 0,
        "mean_p_deg_rs": float(gdf["p_deg_rs"].mean()),
        "p95_p_deg_rs": float(gdf["p_deg_rs"].quantile(0.95)),
        "p_deg_source_counts": {
            str(key): int(value) for key, value in gdf["p_deg_rs_source"].value_counts(dropna=False).items()
        },
        "model": model_meta,
        "parameters": {
            "edge_decay_km": args.edge_decay_km,
            "pca_components": args.pca_components,
            "sentinel_features": str(args.sentinel_features) if args.sentinel_features else None,
            "force_proxy": bool(args.force_proxy),
        },
        "outputs": {
            "csv": str(csv_path),
            "geojson": str(geojson_path),
            "solver_features_csv": str(solver_path),
            "interactive_map": map_path,
            "summary_json": str(summary_path),
        },
        "next_rs_upgrade": [
            "Add additional quarterly Sentinel-2 NDVI/NDWI/NBR composites as Drive exports finish.",
            "Add additional Sentinel-1 VV/VH texture features for cloudy/rainy periods.",
            "Replace the weighted Sentinel score with a convolutional autoencoder over spatiotemporal tensors.",
            "Add a spatial DiD layer once pre/post infrastructure dates and outcomes are available.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local RS Phase 2 edge-effect proxy over the SCN H3 baseline.")
    parser.add_argument("--edge-decay-km", type=float, default=5.0, help="Distance decay for protected/indigenous edge exposure.")
    parser.add_argument("--pca-components", type=int, default=3, help="PCA components for reconstruction-error proxy.")
    parser.add_argument(
        "--sentinel-features",
        type=Path,
        default=DEFAULT_SENTINEL_FEATURES,
        help="Optional H3-level Sentinel feature table extracted from GEE GeoTIFFs.",
    )
    parser.add_argument(
        "--alphaearth-features",
        type=Path,
        default=None,
        help="Optional per-H3 AlphaEarth table from scripts/ingest_alphaearth_h3.py (.parquet or .csv).",
    )
    parser.add_argument(
        "--aef-weight",
        type=float,
        default=0.0,
        help="Blend weight (0-1) of the AlphaEarth change signal into rs_sentinel_signal. 0 merges columns without changing scores.",
    )
    parser.add_argument(
        "--force-proxy",
        action="store_true",
        help="Ignore any Sentinel feature table and run the original local proxy only.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    gdf = load_baseline()
    gdf = add_lulc_labels(gdf)
    gdf = add_fray_flags(gdf)
    gdf = nearest_constraint_context(gdf)
    gdf = add_rs_feature_proxies(gdf, args.edge_decay_km)
    gdf, model_meta = add_pca_autoencoder_proxy(gdf, args.pca_components)
    sentinel_path = None if args.force_proxy else args.sentinel_features
    gdf, sentinel_meta = add_sentinel_features(gdf, sentinel_path)
    gdf, aef_meta = add_alphaearth_features(gdf, None if args.force_proxy else args.alphaearth_features, args.aef_weight)
    sentinel_meta["alphaearth"] = aef_meta
    gdf = add_degradation_probability(gdf, use_sentinel=bool(sentinel_meta.get("enabled")), aef_weight=args.aef_weight if aef_meta.get("enabled") else 0.0)
    summary = write_outputs(gdf, model_meta, sentinel_meta, args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    preview_cols = [
        "h3_id",
        "p_deg_rs",
        "boundary_fray_cell",
        "rs_edge_exposure",
        "rs_reconstruction_error_norm",
        "class_name",
        "nearest_constraint_km",
        "nearest_constraint_name",
    ]
    if "rs_aef_change_signal" in gdf.columns:
        preview_cols.insert(2, "rs_aef_change_signal")
    water_share = pd.to_numeric(gdf["water_surface_share_proxy"], errors="coerce").fillna(0)
    buildable = gdf[~gdf["hard_exclusion"].astype(bool) & (water_share < 0.5)]
    print(f"\nHighest RS Phase 2 edge-effect candidates among {len(buildable):,} non-excluded land cells")
    print(buildable.sort_values("p_deg_rs", ascending=False)[preview_cols].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
