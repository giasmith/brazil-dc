from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import folium
import geopandas as gpd
import pandas as pd
import requests
from folium.plugins import Fullscreen, MarkerCluster, MeasureControl, MiniMap


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "osm"
OUT_DIR = ROOT / "clean_data" / "osm_data_centers"
RAW_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

OVERPASS_QUERY = """
[out:json][timeout:300];
area["ISO3166-1"="BR"]["admin_level"="2"]->.br;
(
  node["telecom"~"^data_cent(er|re)$"](area.br);
  way["telecom"~"^data_cent(er|re)$"](area.br);
  relation["telecom"~"^data_cent(er|re)$"](area.br);
);
out tags center geom;
""".strip()

SELECTED_TAGS = [
    "name",
    "operator",
    "owner",
    "brand",
    "telecom",
    "building",
    "man_made",
    "website",
    "contact:website",
    "phone",
    "contact:phone",
    "email",
    "contact:email",
    "addr:housenumber",
    "addr:street",
    "addr:suburb",
    "addr:city",
    "addr:state",
    "addr:postcode",
    "addr:country",
    "source",
]


def fetch_overpass() -> tuple[dict[str, Any], str]:
    query_path = RAW_DIR / "brazil_data_centers_overpass_query.overpassql"
    query_path.write_text(OVERPASS_QUERY + "\n")

    headers = {
        "User-Agent": "BrazilEnergyResearch/1.0 (OSM data center extract; contact local research project)",
    }
    errors = []
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            response = requests.post(
                endpoint,
                data={"data": OVERPASS_QUERY},
                headers=headers,
                timeout=(20, 360),
            )
            response.raise_for_status()
            return response.json(), endpoint
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")

    error_text = "\n".join(errors)
    raise RuntimeError(f"All Overpass endpoints failed:\n{error_text}")


def clean_column(tag: str) -> str:
    return "tag_" + "".join(ch if ch.isalnum() else "_" for ch in tag).strip("_")


def point_from_element(element: dict[str, Any]) -> tuple[float | None, float | None, str]:
    if element.get("type") == "node":
        return element.get("lon"), element.get("lat"), "node"
    center = element.get("center") or {}
    if "lon" in center and "lat" in center:
        return center.get("lon"), center.get("lat"), "overpass_center"
    bounds = element.get("bounds") or {}
    if {"minlon", "maxlon", "minlat", "maxlat"}.issubset(bounds):
        lon = (bounds["minlon"] + bounds["maxlon"]) / 2
        lat = (bounds["minlat"] + bounds["maxlat"]) / 2
        return lon, lat, "bounds_center"
    return None, None, "missing"


def elements_to_gdf(raw: dict[str, Any]) -> gpd.GeoDataFrame:
    rows = []
    for element in raw.get("elements", []):
        lon, lat, geometry_source = point_from_element(element)
        if lon is None or lat is None:
            continue
        tags = element.get("tags", {})
        osm_type = element.get("type")
        osm_id = element.get("id")
        row = {
            "osm_type": osm_type,
            "osm_id": osm_id,
            "osm_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
            "name": tags.get("name") or tags.get("operator") or f"{osm_type}/{osm_id}",
            "latitude": lat,
            "longitude": lon,
            "geometry_source": geometry_source,
            "tags_json": json.dumps(tags, ensure_ascii=False, sort_keys=True),
        }
        for tag in SELECTED_TAGS:
            row[clean_column(tag)] = tags.get(tag)
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return gpd.GeoDataFrame(df, geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs="EPSG:4326",
    )


def add_state_assignments(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.empty:
        gdf["state_abbrev"] = pd.Series(dtype="object")
        gdf["state_name"] = pd.Series(dtype="object")
        return gdf

    territorial = ROOT / "clean_data" / "brazil_territorial_layers.gpkg"
    if not territorial.exists():
        gdf["state_abbrev"] = None
        gdf["state_name"] = None
        return gdf

    try:
        layers = gpd.read_file(territorial, layer="all_layers")
    except Exception:
        gdf["state_abbrev"] = None
        gdf["state_name"] = None
        return gdf

    states = layers[layers.get("source_layer").eq("states")].copy()
    if states.empty:
        gdf["state_abbrev"] = None
        gdf["state_name"] = None
        return gdf

    keep = [col for col in ["abbrev_state", "name_state", "geometry"] if col in states.columns]
    joined = gpd.sjoin(gdf, states[keep], how="left", predicate="within")
    joined = joined.drop(columns=[col for col in ["index_right"] if col in joined.columns])
    joined = joined.rename(columns={"abbrev_state": "state_abbrev", "name_state": "state_name"})
    return joined


def save_outputs(gdf: gpd.GeoDataFrame, raw: dict[str, Any], endpoint: str) -> dict[str, Any]:
    raw_path = RAW_DIR / "brazil_data_centers_overpass_raw.json"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))

    geojson_path = OUT_DIR / "brazil_osm_data_centers.geojson"
    gpkg_path = OUT_DIR / "brazil_osm_data_centers.gpkg"
    csv_path = OUT_DIR / "brazil_osm_data_centers.csv"
    summary_path = OUT_DIR / "brazil_osm_data_centers_summary.json"

    for path in [geojson_path, gpkg_path]:
        if path.exists():
            path.unlink()

    gdf.to_file(geojson_path, driver="GeoJSON")
    gdf.to_file(gpkg_path, layer="data_centers", driver="GPKG")
    gdf.drop(columns="geometry").to_csv(csv_path, index=False)

    by_state = (
        gdf.groupby(["state_abbrev", "state_name"], dropna=False)
        .size()
        .reset_index(name="osm_data_center_count")
        .sort_values("osm_data_center_count", ascending=False)
    )
    state_path = OUT_DIR / "brazil_osm_data_centers_by_state.csv"
    by_state.to_csv(state_path, index=False)

    summary = {
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "OpenStreetMap via Overpass API",
        "overpass_endpoint": endpoint,
        "overpass_query_file": str(RAW_DIR / "brazil_data_centers_overpass_query.overpassql"),
        "strict_tag_definition": "OSM elements in Brazil with telecom=data_center or telecom=data_centre.",
        "feature_count": int(len(gdf)),
        "osm_element_type_counts": gdf["osm_type"].value_counts(dropna=False).to_dict() if not gdf.empty else {},
        "state_counts_csv": str(state_path),
        "raw_json": str(raw_path),
        "clean_geojson": str(geojson_path),
        "clean_gpkg": str(gpkg_path),
        "clean_csv": str(csv_path),
        "interactive_map": str(OUT_DIR / "brazil_osm_data_centers_map.html"),
        "geofabrik_brazil_pbf_fallback": "https://download.geofabrik.de/south-america/brazil-latest.osm.pbf",
        "license_note": "OpenStreetMap data is available under the Open Database License; attribute OpenStreetMap contributors.",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def popup_html(row: pd.Series) -> str:
    fields = [
        ("Name", row.get("name")),
        ("Operator", row.get("tag_operator")),
        ("Telecom", row.get("tag_telecom")),
        ("State", row.get("state_abbrev")),
        ("City", row.get("tag_addr_city")),
        ("OSM", f"<a href='{html.escape(str(row.get('osm_url')))}' target='_blank'>open object</a>"),
    ]
    rows = []
    for label, value in fields:
        if value is None or pd.isna(value) or str(value).strip() == "":
            continue
        rendered = value if label == "OSM" else html.escape(str(value))
        rows.append(f"<tr><th>{html.escape(label)}</th><td>{rendered}</td></tr>")
    return "<table>" + "".join(rows) + "</table>"


def make_map(gdf: gpd.GeoDataFrame) -> None:
    m = folium.Map(location=[-14.2, -51.9], zoom_start=4, tiles="CartoDB positron")
    Fullscreen(position="topright").add_to(m)
    MiniMap(toggle_display=True, minimized=True).add_to(m)
    MeasureControl(position="bottomleft").add_to(m)

    title = """
    <div style="position: fixed; top: 16px; left: 50px; z-index: 9999;
                background: white; border: 1px solid #999; border-radius: 6px;
                padding: 10px 12px; font-family: Arial, sans-serif; max-width: 360px;">
      <div style="font-size: 16px; font-weight: 700;">Brazil OSM Data Centers</div>
      <div style="font-size: 12px; color: #444;">telecom=data_center / data_centre from OpenStreetMap via Overpass</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title))

    cluster = MarkerCluster(name="OSM data centers").add_to(m)
    for _, row in gdf.iterrows():
        folium.Marker(
            location=[row["latitude"], row["longitude"]],
            tooltip=str(row.get("name") or "OSM data center"),
            popup=folium.Popup(popup_html(row), max_width=420),
            icon=folium.Icon(color="blue", icon="cloud", prefix="fa"),
        ).add_to(cluster)

    folium.LayerControl(collapsed=False).add_to(m)
    m.save(OUT_DIR / "brazil_osm_data_centers_map.html")


def main() -> None:
    raw, endpoint = fetch_overpass()
    gdf = elements_to_gdf(raw)
    gdf = add_state_assignments(gdf)
    summary = save_outputs(gdf, raw, endpoint)
    make_map(gdf)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not gdf.empty:
        display_cols = ["name", "state_abbrev", "tag_operator", "osm_type", "osm_id", "latitude", "longitude"]
        print("\nDownloaded OSM data centers")
        print(gdf[[col for col in display_cols if col in gdf.columns]].to_string(index=False))


if __name__ == "__main__":
    main()
