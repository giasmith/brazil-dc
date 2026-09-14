from __future__ import annotations

import html
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from folium.plugins import Fullscreen, MarkerCluster, MeasureControl, MiniMap


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "idc"
OUT_DIR = ROOT / "clean_data" / "idc_data_centers"
OSM_DIR = ROOT / "clean_data" / "osm_data_centers"
RAW_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

PEERINGDB_FAC_URLS = [
    "https://www.peeringdb.com/api/fac?country=BR&limit=10000",
    "https://peeringdb.com/api/fac?country=BR&limit=10000",
    "https://www.peeringdb.org/api/fac?country=BR&limit=10000",
    "https://monkeyingdb.com/api/fac?country=BR&limit=10000",
    "https://tools.peeringdb.com/api/fac?country=BR&limit=10000",
]
PEERINGDB_KMZ_URL = "https://public.peeringdb.com/peeringdb.kmz"
PEERINGDB_RAW_PATH = RAW_DIR / "peeringdb_brazil_facilities_raw.json"
PEERINGDB_KMZ_PATH = RAW_DIR / "peeringdb_facilities_public.kmz"
PEERINGDB_KML_PATH = RAW_DIR / "peeringdb_facilities_public_doc.kml"


def fetch_peeringdb_facilities() -> dict[str, Any]:
    if PEERINGDB_RAW_PATH.exists():
        cached = json.loads(PEERINGDB_RAW_PATH.read_text())
        if cached.get("data"):
            return cached
        if cached.get("_source_format") == "kmz_kml" and PEERINGDB_KML_PATH.exists():
            cached["kml"] = PEERINGDB_KML_PATH.read_text()
            return cached

    headers = {"User-Agent": "BrazilEnergyResearch/1.0 (Brazil IDC coordinate download)"}
    errors = []
    for url in PEERINGDB_FAC_URLS:
        try:
            response = requests.get(url, headers=headers, timeout=(20, 120))
            response.raise_for_status()
            raw = response.json()
            if raw.get("data"):
                raw["_download_url"] = url
                raw["_source_format"] = "api_json"
                PEERINGDB_RAW_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2))
                return raw
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    try:
        response = requests.get(PEERINGDB_KMZ_URL, headers=headers, timeout=(20, 180))
        response.raise_for_status()
        PEERINGDB_KMZ_PATH.write_bytes(response.content)
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            kml_name = next(name for name in zf.namelist() if name.lower().endswith(".kml"))
            kml_text = zf.read(kml_name).decode("utf-8", errors="replace")
        PEERINGDB_KML_PATH.write_text(kml_text)
        raw = {"_source_format": "kmz_kml", "_download_url": PEERINGDB_KMZ_URL, "kml": kml_text, "api_errors": errors}
        PEERINGDB_RAW_PATH.write_text(json.dumps({k: v for k, v in raw.items() if k != "kml"}, ensure_ascii=False, indent=2))
        return raw
    except Exception as exc:
        errors.append(f"{PEERINGDB_KMZ_URL}: {exc}")

    raise RuntimeError("All PeeringDB facility endpoints and KMZ fallback failed:\n" + "\n".join(errors))


def normalize_peeringdb_kmz(raw: dict[str, Any]) -> gpd.GeoDataFrame:
    kml_text = raw.get("kml")
    if not kml_text and PEERINGDB_KML_PATH.exists():
        kml_text = PEERINGDB_KML_PATH.read_text()
    if not kml_text:
        return gpd.GeoDataFrame(columns=[], geometry=[], crs="EPSG:4326")

    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    root = ET.fromstring(kml_text.encode("utf-8"))
    rows = []
    for placemark in root.findall(".//kml:Placemark", ns):
        fields: dict[str, str | None] = {}
        for data in placemark.findall(".//kml:ExtendedData/kml:Data", ns):
            key = data.attrib.get("name")
            value = data.findtext("kml:value", default="", namespaces=ns)
            if key:
                fields[key] = value
        if fields.get("country") != "BR":
            continue

        coords_text = placemark.findtext(".//kml:Point/kml:coordinates", default="", namespaces=ns).strip()
        lon = lat = None
        if coords_text:
            pieces = coords_text.split(",")
            if len(pieces) >= 2:
                lon = pd.to_numeric(pieces[0], errors="coerce")
                lat = pd.to_numeric(pieces[1], errors="coerce")
        lat = pd.to_numeric(fields.get("latitude"), errors="coerce") if pd.notna(pd.to_numeric(fields.get("latitude"), errors="coerce")) else lat
        lon = pd.to_numeric(fields.get("longitude"), errors="coerce") if pd.notna(pd.to_numeric(fields.get("longitude"), errors="coerce")) else lon
        if pd.isna(lat) or pd.isna(lon):
            continue

        peering_url = fields.get("peeringDB")
        facility_id = ""
        if peering_url and "/fac/" in peering_url:
            facility_id = peering_url.rstrip("/").split("/fac/")[-1]
        name = fields.get("name") or placemark.findtext("kml:name", default="", namespaces=ns)
        rows.append(
            {
                "data_center_id": f"peeringdb_fac_{facility_id or len(rows)}",
                "source": "PeeringDB",
                "source_dataset": "PeeringDB public KMZ facility coordinate file",
                "source_url": peering_url,
                "facility_name": name,
                "operator": name,
                "facility_type": "internet_interconnection_facility",
                "address": fields.get("Address"),
                "city": fields.get("city"),
                "state": fields.get("state"),
                "country": fields.get("country"),
                "postal_code": fields.get("zipcode"),
                "latitude": float(lat),
                "longitude": float(lon),
                "coordinate_quality": "facility_coordinate_from_public_kmz",
                "net_count": pd.to_numeric(fields.get("Networks"), errors="coerce"),
                "ix_count": pd.to_numeric(fields.get("Exchanges"), errors="coerce"),
                "carrier_count": pd.NA,
                "website": fields.get("website"),
                "updated": pd.NA,
                "created": pd.NA,
            }
        )

    df = pd.DataFrame(rows)
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["longitude"], df["latitude"]), crs="EPSG:4326")


def normalize_peeringdb(raw: dict[str, Any]) -> gpd.GeoDataFrame:
    if raw.get("_source_format") == "kmz_kml":
        return normalize_peeringdb_kmz(raw)

    df = pd.DataFrame(raw.get("data", []))
    if df.empty:
        return gpd.GeoDataFrame(df, geometry=[], crs="EPSG:4326")

    df["latitude"] = pd.to_numeric(df.get("latitude"), errors="coerce")
    df["longitude"] = pd.to_numeric(df.get("longitude"), errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"]).copy()
    df = df[df["latitude"].between(-35, 6) & df["longitude"].between(-75, -25)].copy()

    df["source"] = "PeeringDB"
    df["source_dataset"] = "PeeringDB public facility API"
    df["source_url"] = df["id"].map(lambda x: f"https://www.peeringdb.com/fac/{int(x)}")
    df["data_center_id"] = df["id"].map(lambda x: f"peeringdb_fac_{int(x)}")
    df["operator"] = df.get("org_name")
    df["facility_name"] = df.get("name")
    df["city"] = df.get("city")
    df["state"] = df.get("state")
    df["country"] = df.get("country")
    df["address"] = (
        df.get("address1", "").fillna("").astype(str).str.strip()
        + " "
        + df.get("address2", "").fillna("").astype(str).str.strip()
    ).str.strip()
    df["postal_code"] = df.get("zipcode")
    df["coordinate_quality"] = "facility_coordinate_from_source"
    df["facility_type"] = "internet_interconnection_facility"
    df["net_count"] = pd.to_numeric(df.get("net_count"), errors="coerce")
    df["ix_count"] = pd.to_numeric(df.get("ix_count"), errors="coerce")
    df["carrier_count"] = pd.to_numeric(df.get("carrier_count"), errors="coerce")
    df["updated"] = df.get("updated")
    df["created"] = df.get("created")

    keep = [
        "data_center_id",
        "source",
        "source_dataset",
        "source_url",
        "facility_name",
        "operator",
        "facility_type",
        "address",
        "city",
        "state",
        "country",
        "postal_code",
        "latitude",
        "longitude",
        "coordinate_quality",
        "net_count",
        "ix_count",
        "carrier_count",
        "website",
        "updated",
        "created",
    ]
    gdf = gpd.GeoDataFrame(
        df[[col for col in keep if col in df.columns]],
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs="EPSG:4326",
    )
    return gdf


def load_osm_secondary() -> gpd.GeoDataFrame:
    path = OSM_DIR / "brazil_osm_data_centers.geojson"
    if not path.exists():
        return gpd.GeoDataFrame(columns=[], geometry=[], crs="EPSG:4326")

    osm = gpd.read_file(path).to_crs("EPSG:4326")
    if osm.empty:
        return gpd.GeoDataFrame(columns=[], geometry=[], crs="EPSG:4326")

    rows = pd.DataFrame(
        {
            "data_center_id": "osm_" + osm["osm_type"].astype(str) + "_" + osm["osm_id"].astype(str),
            "source": "OpenStreetMap",
            "source_dataset": "OSM Overpass telecom=data_center/data_centre",
            "source_url": osm.get("osm_url"),
            "facility_name": osm.get("name"),
            "operator": osm.get("tag_operator"),
            "facility_type": "osm_telecom_data_center",
            "address": (
                osm.get("tag_addr_street", "").fillna("").astype(str).str.strip()
                + " "
                + osm.get("tag_addr_housenumber", "").fillna("").astype(str).str.strip()
            ).str.strip(),
            "city": osm.get("tag_addr_city"),
            "state": osm.get("state_abbrev"),
            "country": "BR",
            "postal_code": osm.get("tag_addr_postcode"),
            "latitude": pd.to_numeric(osm.get("latitude"), errors="coerce"),
            "longitude": pd.to_numeric(osm.get("longitude"), errors="coerce"),
            "coordinate_quality": "osm_feature_centroid_or_node",
            "net_count": pd.NA,
            "ix_count": pd.NA,
            "carrier_count": pd.NA,
            "website": osm.get("tag_website").combine_first(osm.get("tag_contact_website")),
            "updated": pd.NA,
            "created": pd.NA,
        }
    )
    return gpd.GeoDataFrame(
        rows,
        geometry=gpd.points_from_xy(rows["longitude"], rows["latitude"]),
        crs="EPSG:4326",
    )


def add_state_names(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    gdf["state_from_source"] = gdf["state"]

    territorial = ROOT / "clean_data" / "brazil_territorial_layers.gpkg"
    if not territorial.exists():
        gdf["state_abbrev_spatial"] = None
        gdf["state_name_spatial"] = None
        return gdf

    try:
        layers = gpd.read_file(territorial, layer="all_layers").to_crs("EPSG:4326")
    except Exception:
        gdf["state_abbrev_spatial"] = None
        gdf["state_name_spatial"] = None
        return gdf

    states = layers[layers.get("source_layer").eq("states")].copy()
    if states.empty:
        gdf["state_abbrev_spatial"] = None
        gdf["state_name_spatial"] = None
        return gdf

    keep = [col for col in ["abbrev_state", "name_state", "geometry"] if col in states.columns]
    joined = gpd.sjoin(gdf, states[keep], how="left", predicate="within")
    joined = joined.drop(columns=[col for col in ["index_right"] if col in joined.columns])
    joined = joined.rename(columns={"abbrev_state": "state_abbrev_spatial", "name_state": "state_name_spatial"})
    joined["state"] = joined["state"].fillna(joined["state_abbrev_spatial"])
    return joined


def dedupe_sources(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    peering = gdf[gdf["source"].eq("PeeringDB")].copy()
    other = gdf[~gdf["source"].eq("PeeringDB")].copy()

    if not peering.empty and not other.empty:
        p_lat = peering["latitude"].to_numpy(dtype=float)
        p_lon = peering["longitude"].to_numpy(dtype=float)
        keep_other = []
        for row in other.itertuples():
            mean_lat = np.radians((p_lat + float(row.latitude)) / 2)
            dx = (p_lon - float(row.longitude)) * 111.32 * np.cos(mean_lat)
            dy = (p_lat - float(row.latitude)) * 110.57
            min_km = float(np.sqrt(dx**2 + dy**2).min())
            keep_other.append(min_km > 0.25)
        other = other.loc[keep_other].copy()

    if not other.empty:
        other["rounded_lat"] = other["latitude"].round(5)
        other["rounded_lon"] = other["longitude"].round(5)
        other = other.drop_duplicates(["rounded_lat", "rounded_lon", "facility_name"], keep="first")
        other = other.drop(columns=["rounded_lat", "rounded_lon"], errors="ignore")

    deduped = pd.concat([peering, other], ignore_index=True)
    deduped["data_center_id"] = [f"idc_br_{idx:04d}" for idx in range(len(deduped))]
    return deduped


def save_layers(peeringdb: gpd.GeoDataFrame, osm: gpd.GeoDataFrame, merged: gpd.GeoDataFrame, raw: dict[str, Any]) -> dict[str, Any]:
    raw_path = PEERINGDB_RAW_PATH
    raw_path.write_text(json.dumps({k: v for k, v in raw.items() if k != "kml"}, ensure_ascii=False, indent=2))

    peeringdb_csv = OUT_DIR / "brazil_idc_points_peeringdb.csv"
    peeringdb_geojson = OUT_DIR / "brazil_idc_points_peeringdb.geojson"
    merged_csv = OUT_DIR / "brazil_idc_points_merged.csv"
    merged_geojson = OUT_DIR / "brazil_idc_points_merged.geojson"
    merged_gpkg = OUT_DIR / "brazil_idc_points_merged.gpkg"
    state_csv = OUT_DIR / "brazil_idc_points_by_state.csv"
    source_csv = OUT_DIR / "brazil_idc_points_by_source.csv"
    summary_path = OUT_DIR / "brazil_idc_points_summary.json"

    for path in [peeringdb_geojson, merged_geojson, merged_gpkg]:
        if path.exists():
            path.unlink()

    peeringdb.drop(columns="geometry").to_csv(peeringdb_csv, index=False)
    peeringdb.to_file(peeringdb_geojson, driver="GeoJSON")
    merged.drop(columns="geometry").to_csv(merged_csv, index=False)
    merged.to_file(merged_geojson, driver="GeoJSON")
    merged.to_file(merged_gpkg, layer="idc_points", driver="GPKG")

    by_state = (
        merged.assign(state_report=merged["state"].fillna(merged.get("state_abbrev_spatial")))
        .groupby(["state_report", "source"], dropna=False)
        .size()
        .reset_index(name="facility_count")
        .sort_values(["facility_count", "state_report"], ascending=[False, True])
    )
    by_state.to_csv(state_csv, index=False)

    by_source = (
        merged.groupby("source", dropna=False)
        .agg(
            facility_count=("data_center_id", "count"),
            net_count_sum=("net_count", "sum"),
            carrier_count_sum=("carrier_count", "sum"),
            ix_count_sum=("ix_count", "sum"),
        )
        .reset_index()
        .sort_values("facility_count", ascending=False)
    )
    by_source.to_csv(source_csv, index=False)

    summary = {
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "goal": "At least 200 Brazil IDC/data-center point coordinates.",
        "primary_source": "PeeringDB public facility data",
        "primary_source_url": raw.get("_download_url") or PEERINGDB_FAC_URLS[0],
        "secondary_source": "OpenStreetMap telecom=data_center/data_centre layer already downloaded via Overpass",
        "peeringdb_source_format": raw.get("_source_format", "api_json"),
        "peeringdb_raw_facility_count": len(raw.get("data", [])) if raw.get("data") else int(len(peeringdb)),
        "peeringdb_coordinate_point_count": int(len(peeringdb)),
        "osm_coordinate_point_count": int(len(osm)),
        "merged_deduped_point_count": int(len(merged)),
        "meets_200_point_requirement": bool(len(peeringdb) >= 200 or len(merged) >= 200),
        "raw_peeringdb_json": str(raw_path),
        "raw_peeringdb_kmz": str(PEERINGDB_KMZ_PATH) if PEERINGDB_KMZ_PATH.exists() else None,
        "raw_peeringdb_kml": str(PEERINGDB_KML_PATH) if PEERINGDB_KML_PATH.exists() else None,
        "peeringdb_points_csv": str(peeringdb_csv),
        "peeringdb_points_geojson": str(peeringdb_geojson),
        "merged_points_csv": str(merged_csv),
        "merged_points_geojson": str(merged_geojson),
        "merged_points_gpkg": str(merged_gpkg),
        "state_rollup_csv": str(state_csv),
        "source_rollup_csv": str(source_csv),
        "interactive_map": str(OUT_DIR / "brazil_idc_points_map.html"),
        "license_and_use_notes": [
            "PeeringDB is a freely available, user-maintained interconnection database; observe its acceptable-use policy and do not use for unsolicited outreach.",
            "OSM data is available under the Open Database License; attribute OpenStreetMap contributors.",
            "Data Center Map reports 205 Brazil facilities publicly, but full CSV/GeoJSON exports require access, so this workflow does not scrape or redistribute that paid export.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def popup_html(row: pd.Series) -> str:
    fields = [
        ("Name", row.get("facility_name")),
        ("Operator", row.get("operator")),
        ("Source", row.get("source")),
        ("City", row.get("city")),
        ("State", row.get("state")),
        ("Networks", row.get("net_count")),
        ("Carriers", row.get("carrier_count")),
        ("IXPs", row.get("ix_count")),
        ("URL", f"<a href='{html.escape(str(row.get('source_url')))}' target='_blank'>open source record</a>"),
    ]
    body = []
    for label, value in fields:
        if value is None or pd.isna(value) or str(value).strip() == "":
            continue
        rendered = value if label == "URL" else html.escape(str(value))
        body.append(f"<tr><th>{html.escape(label)}</th><td>{rendered}</td></tr>")
    return "<table>" + "".join(body) + "</table>"


def make_map(merged: gpd.GeoDataFrame) -> None:
    m = folium.Map(location=[-14.2, -51.9], zoom_start=4, tiles="CartoDB positron")
    Fullscreen(position="topright").add_to(m)
    MiniMap(toggle_display=True, minimized=True).add_to(m)
    MeasureControl(position="bottomleft").add_to(m)

    title = f"""
    <div style="position: fixed; top: 16px; left: 50px; z-index: 9999;
                background: white; border: 1px solid #999; border-radius: 6px;
                padding: 10px 12px; font-family: Arial, sans-serif; max-width: 390px;">
      <div style="font-size: 16px; font-weight: 700;">Brazil IDC / Data Center Points</div>
      <div style="font-size: 12px; color: #444;">{len(merged):,} deduped points from PeeringDB + OSM</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title))

    colors = {"PeeringDB": "darkblue", "OpenStreetMap": "green"}
    for source, sub in merged.groupby("source"):
        cluster = MarkerCluster(name=f"{source} points ({len(sub)})").add_to(m)
        for _, row in sub.iterrows():
            folium.CircleMarker(
                location=[row["latitude"], row["longitude"]],
                radius=4,
                color=colors.get(source, "gray"),
                fill=True,
                fill_opacity=0.75,
                weight=1,
                tooltip=f"{row.get('facility_name')} | {row.get('city')}",
                popup=folium.Popup(popup_html(row), max_width=440),
            ).add_to(cluster)

    folium.LayerControl(collapsed=False).add_to(m)
    m.save(OUT_DIR / "brazil_idc_points_map.html")


def main() -> None:
    raw = fetch_peeringdb_facilities()
    peeringdb = add_state_names(normalize_peeringdb(raw))
    osm = add_state_names(load_osm_secondary()) if (OSM_DIR / "brazil_osm_data_centers.geojson").exists() else load_osm_secondary()
    merged = pd.concat([peeringdb, osm], ignore_index=True)
    merged = gpd.GeoDataFrame(merged, geometry="geometry", crs="EPSG:4326")
    merged = dedupe_sources(merged)
    summary = save_layers(peeringdb, osm, merged, raw)
    make_map(merged)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nTop states")
    print(pd.read_csv(OUT_DIR / "brazil_idc_points_by_state.csv").head(20).to_string(index=False))
    print("\nSample points")
    cols = ["facility_name", "operator", "source", "city", "state", "latitude", "longitude", "net_count"]
    print(merged[[col for col in cols if col in merged.columns]].head(30).to_string(index=False))


if __name__ == "__main__":
    main()
