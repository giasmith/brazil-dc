from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from shapely.geometry import Point, Polygon, box


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "clean_data" / "sovereign_compute_nexus"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TERRITORIAL_GPKG = ROOT / "clean_data" / "brazil_territorial_layers.gpkg"
ONS_GRID_GPKG = ROOT / "clean_data" / "ons_official_grid" / "ons_official_grid_model.gpkg"
GEM_CURRENT_GPKG = ROOT / "clean_data" / "power_grid" / "brazil_power_grid_model.gpkg"
GEM_PROPOSED_GPKG = ROOT / "clean_data" / "gem_proposed_overlay" / "gem_proposed_overlay_model.gpkg"
IDC_GPKG = ROOT / "clean_data" / "idc_data_centers" / "brazil_idc_points_merged.gpkg"
LULC_VRT = ROOT / "clean_data" / "lulc_water" / "lulc_candidate_2024.vrt"
WATER_VRT = ROOT / "clean_data" / "lulc_water" / "water_surface_2024.vrt"
CURTAILMENT_STATE_CSV = ROOT / "clean_data" / "ons_curtailment_history" / "curtailment_by_state_total.csv"

PROJECTED_CRS = "EPSG:3857"
RENEWABLE_TECHS = {"hydropower", "wind", "solar", "bioenergy"}


def require_h3():
    try:
        import h3  # type: ignore

        return h3
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing required package 'h3'. Install it with:\n"
            "  python3 -m pip install h3\n\n"
            "Then rerun scripts/build_phase1_h3_baseline.py."
        ) from exc


def case_study_box(center_lat: float, center_lon: float, box_km: float) -> gpd.GeoDataFrame:
    point = gpd.GeoSeries([Point(center_lon, center_lat)], crs="EPSG:4326").to_crs(PROJECTED_CRS).iloc[0]
    half_m = box_km * 1000 / 2
    geom = box(point.x - half_m, point.y - half_m, point.x + half_m, point.y + half_m)
    return gpd.GeoDataFrame(
        [{"case_study": "ceara_box", "center_lat": center_lat, "center_lon": center_lon, "box_km": box_km}],
        geometry=[geom],
        crs=PROJECTED_CRS,
    ).to_crs("EPSG:4326")


def h3_cells_from_polygon(h3, polygon: Polygon, resolution: int) -> list[str]:
    coords_lonlat = list(polygon.exterior.coords)
    coords_latlon = [(lat, lon) for lon, lat in coords_lonlat]

    if hasattr(h3, "LatLngPoly") and hasattr(h3, "polygon_to_cells"):
        poly = h3.LatLngPoly(coords_latlon)
        return sorted(h3.polygon_to_cells(poly, resolution))

    geojson = {"type": "Polygon", "coordinates": [[list(coord) for coord in coords_lonlat]]}
    if hasattr(h3, "polyfill_geojson"):
        return sorted(h3.polyfill_geojson(geojson, resolution))
    if hasattr(h3, "polyfill"):
        return sorted(h3.polyfill(geojson, resolution, geo_json_conformant=True))
    raise RuntimeError("Unsupported h3 Python API. Expected h3 v3 or v4 polygon fill functions.")


def h3_boundary(h3, cell: str) -> Polygon:
    if hasattr(h3, "cell_to_boundary"):
        coords = h3.cell_to_boundary(cell)
        lonlat = [(lon, lat) for lat, lon in coords]
    else:
        coords = h3.h3_to_geo_boundary(cell, geo_json=True)
        lonlat = [(lon, lat) for lon, lat in coords]
    return Polygon(lonlat)


def h3_centroid(h3, cell: str) -> tuple[float, float]:
    if hasattr(h3, "cell_to_latlng"):
        lat, lon = h3.cell_to_latlng(cell)
    else:
        lat, lon = h3.h3_to_geo(cell)
    return float(lat), float(lon)


def build_h3_grid(center_lat: float, center_lon: float, box_km: float, resolution: int) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    h3 = require_h3()
    study_box = case_study_box(center_lat, center_lon, box_km)
    cells = h3_cells_from_polygon(h3, study_box.geometry.iloc[0], resolution)
    records = []
    for cell in cells:
        lat, lon = h3_centroid(h3, cell)
        records.append(
            {
                "h3_id": cell,
                "h3_resolution": resolution,
                "center_lat": lat,
                "center_lon": lon,
                "in_case_study_box": True,
                "geometry": h3_boundary(h3, cell),
            }
        )
    grid = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
    return grid, study_box


def load_territorial_layers() -> dict[str, gpd.GeoDataFrame]:
    layers = gpd.read_file(TERRITORIAL_GPKG, layer="all_layers").to_crs("EPSG:4326")
    return {
        "states": layers[layers["source_layer"].eq("states")].copy(),
        "protected": layers[layers["source_layer"].eq("protected_lands")].copy(),
        "indigenous": layers[layers["source_layer"].eq("indigenous_lands")].copy(),
    }


def add_state_and_exclusions(grid: gpd.GeoDataFrame, territorial: dict[str, gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    grid = grid.copy()
    states = territorial["states"][["abbrev_state", "name_state", "name_region", "geometry"]]
    points = grid[["h3_id", "geometry"]].copy()
    points["geometry"] = points.geometry.representative_point()
    state_attrs = (
        gpd.sjoin(points, states, how="left", predicate="within")
        .drop(columns=["index_right", "geometry"], errors="ignore")
        .drop_duplicates("h3_id")
        .rename(columns={"abbrev_state": "state_code", "name_state": "state_name", "name_region": "state_region"})
    )
    joined = grid.merge(state_attrs, on="h3_id", how="left")

    for label, source in [("protected", territorial["protected"]), ("indigenous", territorial["indigenous"])]:
        source = source[["geometry"]].copy()
        if source.empty:
            joined[f"{label}_overlap"] = False
            continue
        hits = gpd.sjoin(joined[["h3_id", "geometry"]], source, how="left", predicate="intersects")
        overlap_ids = set(hits.loc[hits["index_right"].notna(), "h3_id"])
        joined[f"{label}_overlap"] = joined["h3_id"].isin(overlap_ids)

    joined["hard_exclusion"] = joined["protected_overlap"] | joined["indigenous_overlap"]
    joined["outside_state_boundary"] = joined["state_code"].isna()
    joined["hard_exclusion"] = joined["hard_exclusion"] | joined["outside_state_boundary"]
    return joined


def sample_raster_at_centroids(grid: gpd.GeoDataFrame, raster_path: Path, output_col: str) -> pd.Series:
    coords = list(zip(grid["center_lon"], grid["center_lat"]))
    with rasterio.open(raster_path) as src:
        values = [int(v[0]) if len(v) else 0 for v in src.sample(coords)]
    return pd.Series(values, index=grid.index, name=output_col)


def nearest_distance(left: gpd.GeoDataFrame, right: gpd.GeoDataFrame, distance_col: str, right_cols: Iterable[str] = ()) -> pd.DataFrame:
    if right.empty:
        output = pd.DataFrame(index=left.index)
        output[distance_col] = np.nan
        return output
    left_proj = left.to_crs(PROJECTED_CRS)
    right_proj = right.to_crs(PROJECTED_CRS)
    keep = list(right_cols) + ["geometry"]
    joined = gpd.sjoin_nearest(left_proj[["h3_id", "geometry"]], right_proj[keep], how="left", distance_col=f"{distance_col}_m")
    joined[distance_col] = joined[f"{distance_col}_m"] / 1000
    result = pd.DataFrame(joined.drop(columns=["geometry", f"{distance_col}_m"], errors="ignore"))
    result = result.drop_duplicates("h3_id").set_index("h3_id")
    return result.reindex(left["h3_id"])


def add_infrastructure_features(grid: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    grid = grid.copy()
    ons_buses = gpd.read_file(ONS_GRID_GPKG, layer="buses").to_crs("EPSG:4326")
    ons_lines = gpd.read_file(ONS_GRID_GPKG, layer="branches").to_crs("EPSG:4326")
    idc = gpd.read_file(IDC_GPKG, layer="idc_points").to_crs("EPSG:4326")

    bus_dist = nearest_distance(grid, ons_buses, "nearest_ons_bus_km", ["bus_id", "nom_subestacao", "voltage_kv", "installed_mw"])
    grid["nearest_ons_bus_km"] = bus_dist["nearest_ons_bus_km"].values
    grid["nearest_ons_bus_id"] = bus_dist.get("bus_id", pd.Series(index=grid.index)).values
    grid["nearest_ons_substation"] = bus_dist.get("nom_subestacao", pd.Series(index=grid.index)).values
    grid["nearest_ons_bus_voltage_kv"] = bus_dist.get("voltage_kv", pd.Series(index=grid.index)).values

    hv_buses = ons_buses[pd.to_numeric(ons_buses["voltage_kv"], errors="coerce") >= 230].copy()
    hv_dist = nearest_distance(grid, hv_buses, "nearest_hv_ons_bus_km", ["bus_id", "nom_subestacao", "voltage_kv"])
    grid["nearest_hv_ons_bus_km"] = hv_dist["nearest_hv_ons_bus_km"].values
    grid["nearest_hv_ons_bus_id"] = hv_dist.get("bus_id", pd.Series(index=grid.index)).values

    line_dist = nearest_distance(grid, ons_lines, "nearest_ons_line_km", ["branch_id", "line_name", "voltage_kv"])
    grid["nearest_ons_line_km"] = line_dist["nearest_ons_line_km"].values
    grid["nearest_ons_line_id"] = line_dist.get("branch_id", pd.Series(index=grid.index)).values
    grid["nearest_ons_line_voltage_kv"] = line_dist.get("voltage_kv", pd.Series(index=grid.index)).values

    idc_dist = nearest_distance(grid, idc, "nearest_idc_km", ["data_center_id", "facility_name", "source"])
    grid["nearest_idc_km"] = idc_dist["nearest_idc_km"].values
    grid["nearest_idc_id"] = idc_dist.get("data_center_id", pd.Series(index=grid.index)).values
    grid["nearest_idc_name"] = idc_dist.get("facility_name", pd.Series(index=grid.index)).values
    return grid


def add_generation_context(grid: gpd.GeoDataFrame, radius_km: float) -> gpd.GeoDataFrame:
    grid = grid.copy()
    current = gpd.read_file(GEM_CURRENT_GPKG, layer="generators").to_crs("EPSG:4326")
    proposed = gpd.read_file(GEM_PROPOSED_GPKG, layer="proposed_generators").to_crs("EPSG:4326")

    current["renewable_mw"] = np.where(current["technology"].isin(RENEWABLE_TECHS), pd.to_numeric(current["capacity_mw"], errors="coerce").fillna(0), 0)
    proposed["renewable_mw"] = np.where(proposed["technology"].isin(RENEWABLE_TECHS), pd.to_numeric(proposed["capacity_mw"], errors="coerce").fillna(0), 0)
    gen = pd.concat(
        [
            current[["technology", "capacity_mw", "renewable_mw", "geometry"]],
            proposed[["technology", "capacity_mw", "renewable_mw", "geometry"]],
        ],
        ignore_index=True,
    )
    gen = gpd.GeoDataFrame(gen, geometry="geometry", crs="EPSG:4326")

    cells_proj = grid.to_crs(PROJECTED_CRS)
    gen_proj = gen.to_crs(PROJECTED_CRS)
    buffers = cells_proj[["h3_id", "geometry"]].copy()
    buffers["geometry"] = buffers.geometry.centroid.buffer(radius_km * 1000)
    hits = gpd.sjoin(gen_proj, buffers, how="inner", predicate="within")
    if hits.empty:
        grid["nearby_renewable_mw"] = 0.0
        grid["nearby_generation_asset_count"] = 0
        return grid

    agg = (
        hits.groupby("h3_id", as_index=False)
        .agg(
            nearby_renewable_mw=("renewable_mw", "sum"),
            nearby_generation_asset_count=("technology", "count"),
        )
    )
    grid = grid.merge(agg, on="h3_id", how="left")
    grid[["nearby_renewable_mw", "nearby_generation_asset_count"]] = grid[["nearby_renewable_mw", "nearby_generation_asset_count"]].fillna(0)
    return grid


def add_curtailment_context(grid: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if not CURTAILMENT_STATE_CSV.exists():
        grid["state_curtailed_mwh"] = 0.0
        return grid
    curtailment = pd.read_csv(CURTAILMENT_STATE_CSV).rename(columns={"id_estado": "state_code", "curtailed_mwh": "state_curtailed_mwh"})
    return grid.merge(curtailment[["state_code", "state_curtailed_mwh"]], on="state_code", how="left").assign(
        state_curtailed_mwh=lambda df: pd.to_numeric(df["state_curtailed_mwh"], errors="coerce").fillna(0)
    )


def add_raster_features(grid: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    grid = grid.copy()
    if LULC_VRT.exists():
        grid["dominant_lulc_class"] = sample_raster_at_centroids(grid, LULC_VRT, "dominant_lulc_class")
    else:
        grid["dominant_lulc_class"] = 0
    if WATER_VRT.exists():
        grid["water_surface_centroid"] = sample_raster_at_centroids(grid, WATER_VRT, "water_surface_centroid")
    else:
        grid["water_surface_centroid"] = 0
    grid["water_surface_share_proxy"] = grid["water_surface_centroid"].astype(float)
    return grid


def build_baseline(args: argparse.Namespace) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict]:
    grid, study_box = build_h3_grid(args.center_lat, args.center_lon, args.box_km, args.h3_resolution)
    territorial = load_territorial_layers()
    grid = add_state_and_exclusions(grid, territorial)
    grid = add_raster_features(grid)
    grid = add_infrastructure_features(grid)
    grid = add_generation_context(grid, args.renewable_radius_km)
    grid = add_curtailment_context(grid)
    grid["source_flags"] = json.dumps(
        {
            "lulc": str(LULC_VRT),
            "water": str(WATER_VRT),
            "territorial": str(TERRITORIAL_GPKG),
            "ons_grid": str(ONS_GRID_GPKG),
            "idc": str(IDC_GPKG),
        }
    )
    summary = {
        "case_study": "ceara_h3_phase1_baseline",
        "center_lat": args.center_lat,
        "center_lon": args.center_lon,
        "box_km": args.box_km,
        "h3_resolution": args.h3_resolution,
        "h3_cell_count": int(len(grid)),
        "hard_exclusion_cells": int(grid["hard_exclusion"].sum()),
        "protected_overlap_cells": int(grid["protected_overlap"].sum()),
        "indigenous_overlap_cells": int(grid["indigenous_overlap"].sum()),
        "outside_state_boundary_cells": int(grid["outside_state_boundary"].sum()),
        "outputs": {
            "csv": str(OUT_DIR / "phase1_h3_baseline.csv"),
            "geojson": str(OUT_DIR / "phase1_h3_baseline.geojson"),
            "study_box_geojson": str(OUT_DIR / "ceara_case_study_box.geojson"),
            "summary_json": str(OUT_DIR / "phase1_h3_baseline_summary.json"),
        },
        "notes": [
            "Raster features are centroid proxies in this first scaffold.",
            "Use exact site coordinates before publication.",
            "If LULC source is still a .crdownload, rerun the LULC loader after the final file is available.",
        ],
    }
    return grid, study_box, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Phase 1 H3 baseline for the Sovereign Compute Nexus case study.")
    parser.add_argument("--center-lat", type=float, default=-3.56, help="Case-study center latitude.")
    parser.add_argument("--center-lon", type=float, default=-38.82, help="Case-study center longitude.")
    parser.add_argument("--box-km", type=float, default=50.0, help="Square case-study width/height in kilometers.")
    parser.add_argument("--h3-resolution", type=int, default=8, help="H3 resolution.")
    parser.add_argument("--renewable-radius-km", type=float, default=25.0, help="Radius for nearby renewable generation aggregation.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    grid, study_box, summary = build_baseline(args)

    csv_path = OUT_DIR / "phase1_h3_baseline.csv"
    geojson_path = OUT_DIR / "phase1_h3_baseline.geojson"
    box_path = OUT_DIR / "ceara_case_study_box.geojson"
    summary_path = OUT_DIR / "phase1_h3_baseline_summary.json"

    if geojson_path.exists():
        geojson_path.unlink()
    if box_path.exists():
        box_path.unlink()

    grid.drop(columns="geometry").to_csv(csv_path, index=False)
    grid.to_file(geojson_path, driver="GeoJSON")
    study_box.to_file(box_path, driver="GeoJSON")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nPreview")
    preview_cols = [
        "h3_id",
        "state_code",
        "protected_overlap",
        "indigenous_overlap",
        "hard_exclusion",
        "dominant_lulc_class",
        "water_surface_share_proxy",
        "nearest_hv_ons_bus_km",
        "nearest_ons_line_km",
        "nearest_idc_km",
        "nearby_renewable_mw",
        "state_curtailed_mwh",
    ]
    print(grid[preview_cols].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
