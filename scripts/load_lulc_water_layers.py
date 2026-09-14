from __future__ import annotations

import json
import math
import os
from pathlib import Path
from xml.sax.saxutils import escape

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from folium.plugins import Fullscreen, MeasureControl, MiniMap
from PIL import Image
from rasterio.enums import Resampling
from rasterio.features import rasterize


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = Path("/Users/nqj5zk/Library/CloudStorage/OneDrive-UniversityofVirginia/brazil/data")
OUT_DIR = ROOT / "clean_data" / "lulc_water"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TERRITORIAL_GPKG = ROOT / "clean_data" / "brazil_territorial_layers.gpkg"
EQUAL_AREA_CRS = "EPSG:6933"

# MapBiomas Brazil class codes commonly used in annual land-cover rasters.
# The candidate LULC raster values match this legend family (3, 4, 15, 21, 39, ...).
LULC_LEGEND = {
    0: ("No data / outside Brazil", "nodata", "#000000"),
    3: ("Forest Formation", "natural_vegetation", "#1f8d49"),
    4: ("Savanna Formation", "natural_vegetation", "#7dc975"),
    5: ("Mangrove", "natural_vegetation", "#04381d"),
    6: ("Floodable Forest", "natural_vegetation", "#026975"),
    9: ("Forest Plantation", "plantation", "#7a5900"),
    11: ("Wetland", "natural_vegetation", "#519799"),
    12: ("Grassland Formation", "natural_vegetation", "#d6bc74"),
    15: ("Pasture", "agriculture_pasture", "#edde8e"),
    20: ("Sugar Cane", "agriculture_pasture", "#db7093"),
    21: ("Mosaic of Uses", "agriculture_pasture", "#ffefc3"),
    23: ("Beach, Dune and Sand", "bare_or_other", "#ffa07a"),
    24: ("Urban Area", "built_or_industrial", "#d4271e"),
    25: ("Other Non-Vegetated Area", "bare_or_other", "#db4d4f"),
    26: ("Water", "water", "#2532e4"),
    29: ("Rocky Outcrop", "bare_or_other", "#ffaa5f"),
    30: ("Mining", "built_or_industrial", "#9c0027"),
    31: ("Aquaculture", "water", "#091077"),
    32: ("Hypersaline Tidal Flat", "bare_or_other", "#fc8114"),
    33: ("River, Lake and Ocean", "water", "#2532e4"),
    39: ("Soybean", "agriculture_pasture", "#f5b3c8"),
    40: ("Rice", "agriculture_pasture", "#c71585"),
    41: ("Other Temporary Crops", "agriculture_pasture", "#f54ca9"),
    46: ("Coffee", "agriculture_pasture", "#d68fe2"),
    47: ("Citrus", "agriculture_pasture", "#9932cc"),
    48: ("Other Perennial Crops", "agriculture_pasture", "#e6ccff"),
    49: ("Wooded Sandbank Vegetation", "natural_vegetation", "#02d659"),
    50: ("Herbaceous Sandbank Vegetation", "natural_vegetation", "#ad5100"),
    62: ("Cotton", "agriculture_pasture", "#ff69b4"),
    75: ("Photovoltaic Power Plant", "built_or_industrial", "#c12100"),
}


def discover_sources() -> tuple[Path, Path]:
    files = [path for path in SOURCE_DIR.iterdir() if path.is_file()]
    raster_like = [path for path in files if path.suffix.lower() in {".tif", ".tiff", ".crdownload"}]
    if not raster_like:
        raise FileNotFoundError(f"No raster-like files found in {SOURCE_DIR}")

    water_candidates = [path for path in raster_like if "water" in path.name.lower()]
    if not water_candidates:
        raise FileNotFoundError(f"No water raster found in {SOURCE_DIR}")
    water_path = sorted(water_candidates, key=lambda p: p.stat().st_size, reverse=True)[0]

    lulc_candidates = [path for path in raster_like if path != water_path]
    if not lulc_candidates:
        raise FileNotFoundError(
            f"No LULC raster found in {SOURCE_DIR}. The only completed raster detected is {water_path.name}."
        )
    lulc_path = sorted(lulc_candidates, key=lambda p: p.stat().st_size, reverse=True)[0]
    return lulc_path, water_path


def inspect_raster(path: Path, label: str) -> dict:
    with rasterio.open(path) as src:
        return {
            "label": label,
            "source_path": str(path),
            "is_partial_browser_download": path.suffix.lower() == ".crdownload",
            "driver": src.driver,
            "crs": str(src.crs),
            "width": src.width,
            "height": src.height,
            "count": src.count,
            "dtype": src.dtypes[0],
            "nodata": src.nodata,
            "bounds": {
                "left": src.bounds.left,
                "bottom": src.bounds.bottom,
                "right": src.bounds.right,
                "top": src.bounds.top,
            },
            "resolution_degrees": [src.res[0], src.res[1]],
            "overviews": src.overviews(1),
            "compression": str(src.compression),
            "block_shapes": src.block_shapes,
            "tags": src.tags(),
        }


def link_source(path: Path, link_name: str) -> Path:
    link = OUT_DIR / link_name
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink(path, link)
    return link


def write_vrt(source_path: Path, vrt_name: str) -> Path:
    vrt_path = OUT_DIR / vrt_name
    with rasterio.open(source_path) as src:
        dtype = {"uint8": "Byte", "int16": "Int16", "uint16": "UInt16", "float32": "Float32"}.get(src.dtypes[0], "Byte")
        transform = src.transform
        geotransform = f"{transform.c}, {transform.a}, {transform.b}, {transform.f}, {transform.d}, {transform.e}"
        nodata = "" if src.nodata is None else f"<NoDataValue>{src.nodata}</NoDataValue>"
        vrt = f"""<VRTDataset rasterXSize="{src.width}" rasterYSize="{src.height}">
  <SRS>{escape(src.crs.to_wkt() if src.crs else "")}</SRS>
  <GeoTransform>{geotransform}</GeoTransform>
  <VRTRasterBand dataType="{dtype}" band="1">
    {nodata}
    <ColorInterp>Gray</ColorInterp>
    <SimpleSource>
      <SourceFilename relativeToVRT="0">{escape(str(source_path))}</SourceFilename>
      <SourceBand>1</SourceBand>
      <SourceProperties RasterXSize="{src.width}" RasterYSize="{src.height}" DataType="{dtype}" BlockXSize="{src.block_shapes[0][1]}" BlockYSize="{src.block_shapes[0][0]}"/>
      <SrcRect xOff="0" yOff="0" xSize="{src.width}" ySize="{src.height}"/>
      <DstRect xOff="0" yOff="0" xSize="{src.width}" ySize="{src.height}"/>
    </SimpleSource>
  </VRTRasterBand>
</VRTDataset>
"""
    vrt_path.write_text(vrt)
    return vrt_path


def read_preview(path: Path, max_dim: int = 2200) -> tuple[np.ndarray, rasterio.Affine, rasterio.coords.BoundingBox]:
    with rasterio.open(path) as src:
        scale = max(src.width / max_dim, src.height / max_dim, 1)
        out_w = max(1, int(src.width / scale))
        out_h = max(1, int(src.height / scale))
        arr = src.read(1, out_shape=(out_h, out_w), resampling=Resampling.nearest)
        transform = src.transform * src.transform.scale(src.width / out_w, src.height / out_h)
        return arr, transform, src.bounds


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def rgba_lulc(arr: np.ndarray) -> np.ndarray:
    rgba = np.zeros((arr.shape[0], arr.shape[1], 4), dtype=np.uint8)
    for code, (_, _, color) in LULC_LEGEND.items():
        if code == 0:
            continue
        mask = arr == code
        if not mask.any():
            continue
        rgba[mask, :3] = hex_to_rgb(color)
        rgba[mask, 3] = 150
    return rgba


def rgba_water(arr: np.ndarray) -> np.ndarray:
    rgba = np.zeros((arr.shape[0], arr.shape[1], 4), dtype=np.uint8)
    mask = arr == 1
    rgba[mask, :3] = (37, 99, 235)
    rgba[mask, 3] = 190
    return rgba


def save_preview_geotiff(path: Path, out_path: Path, arr: np.ndarray, transform: rasterio.Affine) -> None:
    with rasterio.open(path) as src:
        profile = src.profile.copy()
    profile.update(
        driver="GTiff",
        width=arr.shape[1],
        height=arr.shape[0],
        transform=transform,
        compress="lzw",
        tiled=True,
        blockxsize=256,
        blockysize=256,
        count=1,
        dtype=str(arr.dtype),
    )
    if out_path.exists():
        out_path.unlink()
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(arr, 1)


def create_previews(lulc_path: Path, water_path: Path) -> dict:
    lulc_arr, lulc_transform, lulc_bounds = read_preview(lulc_path)
    water_arr, water_transform, water_bounds = read_preview(water_path)

    lulc_png = OUT_DIR / "lulc_2024_preview.png"
    water_png = OUT_DIR / "water_surface_2024_preview.png"
    Image.fromarray(rgba_lulc(lulc_arr), mode="RGBA").save(lulc_png)
    Image.fromarray(rgba_water(water_arr), mode="RGBA").save(water_png)

    lulc_preview_tif = OUT_DIR / "lulc_2024_preview.tif"
    water_preview_tif = OUT_DIR / "water_surface_2024_preview.tif"
    save_preview_geotiff(lulc_path, lulc_preview_tif, lulc_arr, lulc_transform)
    save_preview_geotiff(water_path, water_preview_tif, water_arr, water_transform)

    return {
        "lulc_png": str(lulc_png),
        "water_png": str(water_png),
        "lulc_preview_tif": str(lulc_preview_tif),
        "water_preview_tif": str(water_preview_tif),
        "bounds": [[lulc_bounds.bottom, lulc_bounds.left], [lulc_bounds.top, lulc_bounds.right]],
        "lulc_shape": list(lulc_arr.shape),
        "water_shape": list(water_arr.shape),
    }


def load_states() -> gpd.GeoDataFrame:
    layers = gpd.read_file(TERRITORIAL_GPKG, layer="all_layers")
    states = layers[layers["source_layer"].eq("states")].copy()
    states = states[["abbrev_state", "name_state", "name_region", "geometry"]].to_crs("EPSG:4326")
    states_eq = states.to_crs(EQUAL_AREA_CRS)
    states["state_area_km2"] = states_eq.geometry.area / 1_000_000
    states["state_id"] = np.arange(1, len(states) + 1, dtype=np.int16)
    return states


def downsampled_arrays(lulc_path: Path, water_path: Path, max_dim: int = 4200) -> tuple[np.ndarray, np.ndarray, rasterio.Affine]:
    with rasterio.open(lulc_path) as src:
        scale = max(src.width / max_dim, src.height / max_dim, 1)
        out_w = max(1, int(src.width / scale))
        out_h = max(1, int(src.height / scale))
        lulc = src.read(1, out_shape=(out_h, out_w), resampling=Resampling.nearest)
        transform = src.transform * src.transform.scale(src.width / out_w, src.height / out_h)
    with rasterio.open(water_path) as src:
        water = src.read(1, out_shape=lulc.shape, resampling=Resampling.nearest)
    return lulc, water, transform


def row_area_km2(transform: rasterio.Affine, height: int, width: int) -> np.ndarray:
    res_lon = abs(transform.a)
    res_lat = abs(transform.e)
    rows = np.arange(height)
    lat = transform.f + transform.e * (rows + 0.5)
    km_lon = 111.320 * np.cos(np.deg2rad(lat))
    km_lat = 110.574
    return np.maximum(km_lon, 0) * km_lat * res_lon * res_lat


def zonal_summaries(lulc_path: Path, water_path: Path) -> dict:
    states = load_states()
    lulc, water, transform = downsampled_arrays(lulc_path, water_path)
    shapes = [(geom, int(state_id)) for geom, state_id in zip(states.geometry, states.state_id)]
    state_raster = rasterize(shapes, out_shape=lulc.shape, transform=transform, fill=0, dtype="int16")
    row_area = row_area_km2(transform, lulc.shape[0], lulc.shape[1])
    weights = np.repeat(row_area, lulc.shape[1])

    state_lookup = states.set_index("state_id")[["abbrev_state", "name_state", "name_region", "state_area_km2"]]

    valid_lulc = (state_raster > 0) & (lulc > 0)
    combined = state_raster[valid_lulc].astype(np.int64) * 256 + lulc[valid_lulc].astype(np.int64)
    lulc_counts = np.bincount(combined, weights=weights[valid_lulc.ravel()], minlength=(states["state_id"].max() + 1) * 256)

    rows = []
    for state_id in states["state_id"]:
        state_meta = state_lookup.loc[state_id]
        state_area = state_meta["state_area_km2"]
        for class_code, area_km2 in enumerate(lulc_counts[state_id * 256 : (state_id + 1) * 256]):
            if area_km2 <= 0:
                continue
            class_name, class_group, _ = LULC_LEGEND.get(class_code, (f"Unmapped class {class_code}", "unmapped", "#999999"))
            rows.append(
                {
                    "state_id": state_id,
                    "abbrev_state": state_meta["abbrev_state"],
                    "name_state": state_meta["name_state"],
                    "name_region": state_meta["name_region"],
                    "class_code": class_code,
                    "class_name": class_name,
                    "class_group": class_group,
                    "area_km2": area_km2,
                    "share_of_state_pct": area_km2 / state_area * 100 if state_area else 0,
                }
            )
    lulc_df = pd.DataFrame(rows)

    valid_water = (state_raster > 0) & (water == 1)
    water_area = np.bincount(state_raster[valid_water].astype(np.int64), weights=weights[valid_water.ravel()], minlength=states["state_id"].max() + 1)
    summary_rows = []
    group_totals = lulc_df.groupby(["abbrev_state", "class_group"], as_index=False)["area_km2"].sum()
    group_pivot = group_totals.pivot(index="abbrev_state", columns="class_group", values="area_km2").fillna(0)
    for state in states.itertuples():
        groups = group_pivot.loc[state.abbrev_state] if state.abbrev_state in group_pivot.index else pd.Series(dtype=float)
        summary_rows.append(
            {
                "state_id": state.state_id,
                "abbrev_state": state.abbrev_state,
                "name_state": state.name_state,
                "name_region": state.name_region,
                "state_area_km2": state.state_area_km2,
                "water_surface_area_km2": water_area[state.state_id],
                "water_surface_share_pct": water_area[state.state_id] / state.state_area_km2 * 100,
                "natural_vegetation_area_km2": float(groups.get("natural_vegetation", 0)),
                "agriculture_pasture_area_km2": float(groups.get("agriculture_pasture", 0)),
                "built_or_industrial_area_km2": float(groups.get("built_or_industrial", 0)),
                "plantation_area_km2": float(groups.get("plantation", 0)),
                "water_lulc_area_km2": float(groups.get("water", 0)),
                "dominant_lulc_group": group_pivot.loc[state.abbrev_state].idxmax() if state.abbrev_state in group_pivot.index and not group_pivot.loc[state.abbrev_state].empty else None,
            }
        )
    summary_df = pd.DataFrame(summary_rows)

    class_path = OUT_DIR / "lulc_class_area_by_state.csv"
    summary_path = OUT_DIR / "state_lulc_water_summary.csv"
    legend_path = OUT_DIR / "lulc_class_legend.csv"
    lulc_df.sort_values(["abbrev_state", "area_km2"], ascending=[True, False]).to_csv(class_path, index=False)
    summary_df.sort_values("abbrev_state").to_csv(summary_path, index=False)
    pd.DataFrame(
        [
            {"class_code": code, "class_name": name, "class_group": group, "color": color}
            for code, (name, group, color) in sorted(LULC_LEGEND.items())
        ]
    ).to_csv(legend_path, index=False)

    return {
        "lulc_class_area_by_state": str(class_path),
        "state_lulc_water_summary": str(summary_path),
        "lulc_class_legend": str(legend_path),
        "summary_shape": list(lulc.shape),
        "summary_resolution_degrees": [abs(transform.a), abs(transform.e)],
    }


def make_map(preview: dict) -> Path:
    states = load_states()
    states["geometry"] = states.geometry.simplify(0.025, preserve_topology=True)

    m = folium.Map(location=[-14.2, -51.9], zoom_start=4, tiles="CartoDB positron", prefer_canvas=True)
    Fullscreen(position="topright").add_to(m)
    MiniMap(toggle_display=True, minimized=True).add_to(m)
    MeasureControl(position="bottomleft").add_to(m)

    folium.raster_layers.ImageOverlay(
        name="LULC 2024 preview",
        image=preview["lulc_png"],
        bounds=preview["bounds"],
        opacity=0.68,
        interactive=False,
        cross_origin=False,
        zindex=2,
        show=True,
    ).add_to(m)
    folium.raster_layers.ImageOverlay(
        name="Water surface 2024 preview",
        image=preview["water_png"],
        bounds=preview["bounds"],
        opacity=0.82,
        interactive=False,
        cross_origin=False,
        zindex=3,
        show=True,
    ).add_to(m)
    folium.GeoJson(
        states[["abbrev_state", "name_state", "geometry"]],
        name="State boundaries",
        style_function=lambda _: {"color": "#111827", "weight": 1.2, "fillOpacity": 0},
        tooltip=folium.GeoJsonTooltip(fields=["abbrev_state", "name_state"], aliases=["UF", "State"]),
    ).add_to(m)

    legend_items = ""
    for code in [3, 4, 6, 11, 12, 15, 21, 24, 33, 39, 41, 62, 75]:
        name, _, color = LULC_LEGEND[code]
        legend_items += f'<div><span style="display:inline-block;width:10px;height:10px;background:{color};margin-right:5px;"></span>{code}: {html_escape(name)}</div>'
    title = f"""
    <div style="position: fixed; top: 16px; left: 50px; z-index: 9999;
                background: rgba(255,255,255,0.96); border: 1px solid #999; border-radius: 6px;
                padding: 10px 12px; font-family: Arial, sans-serif; max-width: 430px;">
      <div style="font-size: 17px; font-weight: 700;">Brazil LULC and Water Surface Preview</div>
      <div style="font-size: 12px; color: #444; line-height: 1.35;">
        Lightweight visualization of the OneDrive rasters. The LULC source is currently a readable
        <code>.crdownload</code>, so re-run after the browser download finishes if the final filename changes.
      </div>
    </div>
    <div style="position: fixed; bottom: 28px; left: 28px; z-index: 9999;
                background: rgba(255,255,255,0.94); border: 1px solid #999; border-radius: 6px;
                padding: 8px 10px; font-family: Arial, sans-serif; font-size: 11px; max-height: 280px; overflow-y: auto;">
      <b>LULC class preview</b>
      {legend_items}
      <div><span style="display:inline-block;width:10px;height:10px;background:#2563eb;margin-right:5px;"></span>Water surface raster</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title))
    folium.LayerControl(collapsed=False).add_to(m)

    map_path = OUT_DIR / "lulc_water_preview_map.html"
    m.save(map_path)
    return map_path


def html_escape(value: str) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main() -> None:
    lulc_path, water_path = discover_sources()

    lulc_link = link_source(lulc_path, "source_lulc_candidate_2024.tif")
    water_link = link_source(water_path, "source_water_surface_2024.tif")
    lulc_vrt = write_vrt(lulc_path, "lulc_candidate_2024.vrt")
    water_vrt = write_vrt(water_path, "water_surface_2024.vrt")

    preview = create_previews(lulc_path, water_path)
    zonal = zonal_summaries(lulc_path, water_path)
    map_path = make_map(preview)

    catalog = {
        "source_directory": str(SOURCE_DIR),
        "outputs_directory": str(OUT_DIR),
        "lulc_source": inspect_raster(lulc_path, "candidate_lulc_2024"),
        "water_source": inspect_raster(water_path, "water_surface_2024"),
        "project_links": {
            "lulc_symlink": str(lulc_link),
            "water_symlink": str(water_link),
            "lulc_vrt": str(lulc_vrt),
            "water_vrt": str(water_vrt),
        },
        "preview_outputs": preview,
        "zonal_summary_outputs": zonal,
        "interactive_map": str(map_path),
        "notes": [
            "The LULC raster is currently named 'Unconfirmed 614937.crdownload', which means Chrome may not have completed or renamed the download. It is readable as a BigTIFF and was loaded as candidate LULC.",
            "The LULC class values match the MapBiomas Brazil class-code family. Keep the class legend CSV with any downstream analysis.",
            "State summaries are approximate because they use a downsampled raster grid for speed; use the full VRTs for parcel or exact zonal work.",
            "Raw source rasters remain in OneDrive; this project stores symlinks, VRTs, previews, and summaries.",
        ],
    }
    catalog_path = OUT_DIR / "lulc_water_catalog.json"
    catalog_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False))

    print(json.dumps(catalog, indent=2, ensure_ascii=False))
    print("\nTop water-surface states")
    summary = pd.read_csv(zonal["state_lulc_water_summary"])
    print(summary.sort_values("water_surface_area_km2", ascending=False).head(12).to_string(index=False))
    print(f"\nOutputs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
