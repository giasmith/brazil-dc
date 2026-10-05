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


def case_study_box(center_lat: float, center_lon: float, box_km_ew: float, box_km_ns: float) -> gpd.GeoDataFrame:
    point = gpd.GeoSeries([Point(center_lon, center_lat)], crs="EPSG:4326").to_crs(PROJECTED_CRS).iloc[0]
    half_ew_m = box_km_ew * 1000 / 2
    half_ns_m = box_km_ns * 1000 / 2
    geom = box(point.x - half_ew_m, point.y - half_ns_m, point.x + half_ew_m, point.y + half_ns_m)
    return gpd.GeoDataFrame(
        [{
            "case_study": "ceara_box",
            "center_lat": center_lat,
            "center_lon": center_lon,
            "box_km_ew": box_km_ew,
            "box_km_ns": box_km_ns,
            # kept so the original square runs still round-trip
            "box_km": box_km_ew if box_km_ew == box_km_ns else None,
        }],
        geometry=[geom],
        crs=PROJECTED_CRS,
    ).to_crs("EPSG:4326")


def box_dimensions(args: argparse.Namespace) -> tuple[float, float]:
    """East-west and north-south extents in km. --box-km alone still gives the original square."""
    box_km_ew = args.box_km_ew if args.box_km_ew is not None else args.box_km
    box_km_ns = args.box_km_ns if args.box_km_ns is not None else args.box_km
    if box_km_ew <= 0 or box_km_ns <= 0:
        raise SystemExit("Case-study box dimensions must be positive.")
    return float(box_km_ew), float(box_km_ns)


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


def build_h3_grid(center_lat: float, center_lon: float, box_km_ew: float, box_km_ns: float, resolution: int) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    h3 = require_h3()
    study_box = case_study_box(center_lat, center_lon, box_km_ew, box_km_ns)
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


def add_generation_context(grid: gpd.GeoDataFrame, radius_km: float, occupied_radius_km: float = 1.0, occupied_min_mw: float = 5.0) -> gpd.GeoDataFrame:
    """Nearby renewable capacity split by status, plus an "already occupied by a plant" flag.

    Changed 2026-10-05. Previously one number, nearby_renewable_mw, summed the GEM current and
    proposed layers with equal weight. That had two problems: (1) the 176 under-construction units
    appear in both layers and were double-counted; (2) proposed capacity (7 GW of planned wind in the
    Ceará box, mostly offshore filings) swamped operating capacity, so a cell sitting on a working
    200 MW solar farm scored as energy-poor. Now:
        nearby_renewable_mw_operating     operating units within radius_km
        nearby_renewable_mw_construction  under construction (taken from the current layer only)
        nearby_renewable_mw_proposed      announced / pre-construction (proposed layer only)
        nearby_renewable_mw               the legacy total, kept for continuity = op + construction + proposed
    and, for the occupancy check:
        nearest_operating_plant_km / _name / _mw   nearest operating or under-construction unit >= occupied_min_mw
        occupied_by_generation                      True when that unit is within occupied_radius_km of the cell centre
    Occupancy is a review flag, not an exclusion: a point location cannot tell a 2 ha substation from a
    200 ha solar field, so Phase 4 routes these cells to human review.
    """
    grid = grid.copy()
    current = gpd.read_file(GEM_CURRENT_GPKG, layer="generators").to_crs("EPSG:4326")
    proposed = gpd.read_file(GEM_PROPOSED_GPKG, layer="proposed_generators").to_crs("EPSG:4326")

    def status_group(df: gpd.GeoDataFrame, default: str) -> pd.Series:
        st = df["status_norm"].astype(str).str.lower() if "status_norm" in df.columns else pd.Series(default, index=df.index)
        return np.select([st.eq("operating"), st.eq("construction")], ["operating", "construction"], default="proposed")

    current = current.copy()
    current["status_group"] = status_group(current, "operating")
    proposed = proposed.copy()
    proposed["status_group"] = status_group(proposed, "proposed")
    # under-construction units are listed in both layers: keep the current layer's copy only
    proposed = proposed[proposed["status_group"] == "proposed"]

    for df in (current, proposed):
        df["capacity_mw"] = pd.to_numeric(df["capacity_mw"], errors="coerce").fillna(0)
        df["renewable_mw"] = np.where(df["technology"].isin(RENEWABLE_TECHS), df["capacity_mw"], 0)
        if "asset_name" not in df.columns:
            df["asset_name"] = ""
    gen = pd.concat(
        [
            current[["asset_name", "technology", "status_group", "capacity_mw", "renewable_mw", "geometry"]],
            proposed[["asset_name", "technology", "status_group", "capacity_mw", "renewable_mw", "geometry"]],
        ],
        ignore_index=True,
    )
    gen = gpd.GeoDataFrame(gen, geometry="geometry", crs="EPSG:4326")

    cells_proj = grid.to_crs(PROJECTED_CRS)
    gen_proj = gen.to_crs(PROJECTED_CRS)
    buffers = cells_proj[["h3_id", "geometry"]].copy()
    buffers["geometry"] = buffers.geometry.centroid.buffer(radius_km * 1000)
    hits = gpd.sjoin(gen_proj, buffers, how="inner", predicate="within")

    split_cols = ["nearby_renewable_mw_operating", "nearby_renewable_mw_construction", "nearby_renewable_mw_proposed"]
    if hits.empty:
        for col in split_cols + ["nearby_renewable_mw"]:
            grid[col] = 0.0
        grid["nearby_generation_asset_count"] = 0
    else:
        pivot = hits.pivot_table(index="h3_id", columns="status_group", values="renewable_mw", aggfunc="sum", fill_value=0.0)
        for group, col in (("operating", split_cols[0]), ("construction", split_cols[1]), ("proposed", split_cols[2])):
            pivot[col] = pivot[group] if group in pivot.columns else 0.0
        pivot["nearby_renewable_mw"] = pivot[split_cols].sum(axis=1)
        pivot["nearby_generation_asset_count"] = hits.groupby("h3_id")["technology"].count()
        grid = grid.merge(pivot[split_cols + ["nearby_renewable_mw", "nearby_generation_asset_count"]].reset_index(), on="h3_id", how="left")
        grid[split_cols + ["nearby_renewable_mw", "nearby_generation_asset_count"]] = grid[split_cols + ["nearby_renewable_mw", "nearby_generation_asset_count"]].fillna(0)

    # occupancy: nearest operating / under-construction unit of at least occupied_min_mw to the cell centre
    built = gen_proj[(gen_proj["status_group"].isin(["operating", "construction"])) & (gen_proj["capacity_mw"] >= occupied_min_mw)]
    centres = cells_proj[["h3_id", "geometry"]].copy()
    centres["geometry"] = centres.geometry.centroid
    if built.empty:
        grid["nearest_operating_plant_km"] = np.nan
        grid["nearest_operating_plant_name"] = ""
        grid["nearest_operating_plant_mw"] = np.nan
    else:
        near = gpd.sjoin_nearest(centres, built[["asset_name", "capacity_mw", "technology", "geometry"]], how="left", distance_col="_dist_m")
        near = near.drop_duplicates("h3_id")
        near["nearest_operating_plant_name"] = near["asset_name"].fillna("").astype(str) + " (" + near["technology"].fillna("").astype(str) + ")"
        grid = grid.merge(
            near[["h3_id", "_dist_m", "nearest_operating_plant_name", "capacity_mw"]].rename(columns={"_dist_m": "nearest_operating_plant_km", "capacity_mw": "nearest_operating_plant_mw"}),
            on="h3_id", how="left",
        )
        grid["nearest_operating_plant_km"] = grid["nearest_operating_plant_km"] / 1000.0
    grid["occupied_by_generation"] = grid["nearest_operating_plant_km"].le(occupied_radius_km).fillna(False)
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
    box_km_ew, box_km_ns = box_dimensions(args)
    grid, study_box = build_h3_grid(args.center_lat, args.center_lon, box_km_ew, box_km_ns, args.h3_resolution)
    territorial = load_territorial_layers()
    grid = add_state_and_exclusions(grid, territorial)
    grid = add_raster_features(grid)
    grid = add_infrastructure_features(grid)
    grid = add_generation_context(grid, args.renewable_radius_km, args.occupied_radius_km, args.occupied_min_mw)
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
        "box_km": box_km_ew if box_km_ew == box_km_ns else None,
        "box_km_ew": box_km_ew,
        "box_km_ns": box_km_ns,
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
    parser.add_argument("--box-km", type=float, default=50.0, help="Square case-study width/height in kilometers; used when the two axis flags are omitted.")
    parser.add_argument("--box-km-ew", type=float, default=None, help="East-west extent in kilometers; overrides --box-km.")
    parser.add_argument("--box-km-ns", type=float, default=None, help="North-south extent in kilometers; overrides --box-km.")
    parser.add_argument("--h3-resolution", type=int, default=8, help="H3 resolution.")
    parser.add_argument("--renewable-radius-km", type=float, default=25.0, help="Radius for nearby renewable generation aggregation.")
    parser.add_argument("--occupied-radius-km", type=float, default=1.0, help="A cell whose centre is within this distance of an operating or under-construction plant is flagged occupied_by_generation (review, not exclusion).")
    parser.add_argument("--occupied-min-mw", type=float, default=5.0, help="Smallest plant that counts for the occupancy flag.")
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
        "nearby_renewable_mw_operating",
        "occupied_by_generation",
        "state_curtailed_mwh",
    ]
    print(grid[preview_cols].head(20).to_string(index=False))
    print(f"occupied_by_generation: {int(grid['occupied_by_generation'].sum())} of {len(grid)} cells; "
          f"operating renewable MW within radius: median {grid['nearby_renewable_mw_operating'].median():.0f}, max {grid['nearby_renewable_mw_operating'].max():.0f}; "
          f"proposed: median {grid['nearby_renewable_mw_proposed'].median():.0f}, max {grid['nearby_renewable_mw_proposed'].max():.0f}")


if __name__ == "__main__":
    main()
