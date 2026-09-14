from __future__ import annotations

import json
from pathlib import Path

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
from folium.plugins import Fullscreen, MeasureControl, MiniMap
from shapely.geometry import LineString


ROOT = Path(__file__).resolve().parents[1]
ONS_DIR = ROOT / "data" / "ons"
GRID_DIR = ROOT / "clean_data" / "power_grid"
OUT_DIR = ROOT / "clean_data" / "ons_comparison"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def clean_string(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def classify_ons(row: pd.Series) -> str:
    plant_type = str(row.get("nom_tipousina", "")).upper()
    fuel = str(row.get("nom_combustivel", "")).upper()

    if "HIDRO" in plant_type or "HIDR" in fuel:
        return "hydropower"
    if "EOL" in plant_type or "EÓLICA" in fuel or "EOLICA" in fuel:
        return "wind"
    if "FOTOVOLTAICA" in plant_type or "FOTOVOLTAICA" in fuel:
        return "solar"
    if "NUCLEAR" in plant_type or "NUCLEAR" in fuel:
        return "nuclear"
    if "CARV" in fuel:
        return "coal"
    if "BIOMASSA" in fuel or "BAGA" in fuel:
        return "bioenergy"
    if any(token in fuel for token in ["GÁS", "GAS", "ÓLEO", "OLEO", "DIESEL", "COMBUSTÍVEL", "COMBUSTIVEL"]):
        return "oil_gas"
    if "RESÍDU" in fuel or "RESIDU" in fuel:
        return "other_thermal"
    return "other"


def ons_generation_mix() -> pd.DataFrame:
    cap = pd.read_parquet(ONS_DIR / "capacidade-geracao" / "CAPACIDADE_GERACAO.parquet")
    cap = cap[clean_string(cap["dat_desativacao"]).eq("")].copy()
    cap["technology"] = cap.apply(classify_ons, axis=1)
    cap["installed_mw_ons"] = pd.to_numeric(cap["val_potenciaefetiva"], errors="coerce").fillna(0)
    mix = (
        cap.groupby("technology", as_index=False)
        .agg(
            ons_units=("cod_equipamento", "count"),
            installed_mw_ons=("installed_mw_ons", "sum"),
        )
        .sort_values("installed_mw_ons", ascending=False)
    )
    mix["ons_share_pct"] = mix["installed_mw_ons"] / mix["installed_mw_ons"].sum() * 100
    return mix


def gem_generation_mix() -> pd.DataFrame:
    generators = pd.read_csv(GRID_DIR / "brazil_power_grid_generators.csv")
    mix = (
        generators.groupby("technology", as_index=False)
        .agg(
            gem_assets=("generator_id", "count"),
            installed_mw_gem=("capacity_mw", "sum"),
            modeled_available_mw_gem=("dispatchable_mw", "sum"),
        )
        .sort_values("installed_mw_gem", ascending=False)
    )
    mix["gem_share_pct"] = mix["installed_mw_gem"] / mix["installed_mw_gem"].sum() * 100
    return mix


def compare_generation() -> pd.DataFrame:
    ons = ons_generation_mix()
    gem = gem_generation_mix()
    comparison = ons.merge(gem, on="technology", how="outer").fillna(0)
    comparison["installed_mw_delta_gem_minus_ons"] = comparison["installed_mw_gem"] - comparison["installed_mw_ons"]
    comparison["installed_pct_delta_gem_minus_ons"] = comparison["gem_share_pct"] - comparison["ons_share_pct"]
    comparison = comparison.sort_values("installed_mw_ons", ascending=False)
    comparison.to_csv(OUT_DIR / "generation_mix_ons_vs_gem.csv", index=False)
    return comparison


def build_ons_network_geometries() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    substations = pd.read_parquet(ONS_DIR / "subestacao" / "SUBESTACAO.parquet")
    substations["latitude"] = pd.to_numeric(substations["val_latitude"], errors="coerce")
    substations["longitude"] = pd.to_numeric(substations["val_longitude"], errors="coerce")
    substations = substations.dropna(subset=["latitude", "longitude", "num_barra"]).copy()
    substations = substations[substations["latitude"].between(-35, 6) & substations["longitude"].between(-75, -25)].copy()

    sub_gdf = gpd.GeoDataFrame(
        substations,
        geometry=gpd.points_from_xy(substations["longitude"], substations["latitude"]),
        crs="EPSG:4326",
    )

    lines = pd.read_parquet(ONS_DIR / "linha_transmissao" / "LINHA_TRANSMISSAO.parquet")
    by_bar = sub_gdf.sort_values("val_niveltensao", ascending=False).drop_duplicates("num_barra").set_index("num_barra")

    records = []
    for row in lines.itertuples(index=False):
        if pd.isna(row.num_barra_de) or pd.isna(row.num_barra_para):
            continue
        if row.num_barra_de not in by_bar.index or row.num_barra_para not in by_bar.index:
            continue
        start = by_bar.loc[row.num_barra_de]
        end = by_bar.loc[row.num_barra_para]
        records.append(
            {
                "line_name": row.nom_linhadetransmissao,
                "from_substation": row.nom_subestacao_de,
                "to_substation": row.nom_subestacao_para,
                "from_state": row.id_estado_terminalde,
                "to_state": row.id_estado_terminalpara,
                "from_subsystem": row.id_subsistema_terminalde,
                "to_subsystem": row.id_subsistema_terminalpara,
                "voltage_kv": row.val_niveltensao_kv,
                "length_km_ons": row.val_comprimento,
                "reactance_ohm": row.val_reatancia,
                "long_limit_mw": row.val_capacoperlongacomlimit,
                "short_limit_mw": row.val_capacopercurtacomlimit,
                "owner": row.nom_agenteproprietario,
                "operation_date": row.dat_entradaoperacao,
                "geometry": LineString([start.geometry, end.geometry]),
            }
        )

    line_gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
    sub_keep = [
        "id_subsistema",
        "nom_subsistema",
        "id_estado",
        "nom_estado",
        "nom_agente_principal",
        "id_subestacao",
        "nom_subestacao",
        "val_niveltensao",
        "id_estacao",
        "num_barra",
        "latitude",
        "longitude",
        "geometry",
    ]
    return sub_gdf[sub_keep], line_gdf


def compare_network(ons_substations: gpd.GeoDataFrame, ons_lines: gpd.GeoDataFrame) -> pd.DataFrame:
    gem_buses = pd.read_csv(GRID_DIR / "brazil_power_grid_buses.csv")
    gem_branches = pd.read_csv(GRID_DIR / "brazil_power_grid_branches.csv")

    rows = [
        {
            "metric": "nodes_or_buses",
            "ons_official": len(ons_substations),
            "gem_synthetic": len(gem_buses),
            "delta_gem_minus_ons": len(gem_buses) - len(ons_substations),
        },
        {
            "metric": "branches_or_lines",
            "ons_official": len(ons_lines),
            "gem_synthetic": len(gem_branches),
            "delta_gem_minus_ons": len(gem_branches) - len(ons_lines),
        },
        {
            "metric": "total_line_length_km",
            "ons_official": ons_lines["length_km_ons"].sum(),
            "gem_synthetic": gem_branches["length_km"].sum(),
            "delta_gem_minus_ons": gem_branches["length_km"].sum() - ons_lines["length_km_ons"].sum(),
        },
        {
            "metric": "median_voltage_kv",
            "ons_official": ons_lines["voltage_kv"].median(),
            "gem_synthetic": gem_branches["voltage_kv"].median(),
            "delta_gem_minus_ons": gem_branches["voltage_kv"].median() - ons_lines["voltage_kv"].median(),
        },
    ]
    comparison = pd.DataFrame(rows)
    comparison.to_csv(OUT_DIR / "network_topology_ons_vs_synthetic.csv", index=False)

    voltage = (
        ons_lines.groupby("voltage_kv", as_index=False)
        .agg(ons_lines=("line_name", "count"), ons_length_km=("length_km_ons", "sum"))
        .merge(
            gem_branches.groupby("voltage_kv", as_index=False)
            .agg(gem_branches=("branch_id", "count"), gem_length_km=("length_km", "sum")),
            on="voltage_kv",
            how="outer",
        )
        .fillna(0)
        .sort_values("voltage_kv")
    )
    voltage.to_csv(OUT_DIR / "voltage_class_ons_vs_synthetic.csv", index=False)
    return comparison


def compare_ons_dispatch_to_capacity() -> pd.DataFrame:
    balance = pd.read_parquet(ONS_DIR / "balanco_energia_subsistema_ho" / "BALANCO_ENERGIA_SUBSISTEMA_2025.parquet")
    sin = balance[balance["id_subsistema"].eq("SIN")].copy()
    dispatch = pd.DataFrame(
        {
            "technology": ["hydropower", "oil_gas_and_other_thermal", "wind", "solar"],
            "ons_2025_avg_generation_mw": [
                sin["val_gerhidraulica"].mean(),
                sin["val_gertermica"].mean(),
                sin["val_gereolica"].mean(),
                sin["val_gersolar"].mean(),
            ],
            "ons_2025_peak_generation_mw": [
                sin["val_gerhidraulica"].max(),
                sin["val_gertermica"].max(),
                sin["val_gereolica"].max(),
                sin["val_gersolar"].max(),
            ],
        }
    )
    dispatch["ons_2025_avg_share_pct"] = (
        dispatch["ons_2025_avg_generation_mw"] / dispatch["ons_2025_avg_generation_mw"].sum() * 100
    )
    dispatch.to_csv(OUT_DIR / "ons_2025_dispatch_mix.csv", index=False)
    return dispatch


def write_geodata(ons_substations: gpd.GeoDataFrame, ons_lines: gpd.GeoDataFrame) -> None:
    gpkg = OUT_DIR / "ons_network_layers.gpkg"
    if gpkg.exists():
        gpkg.unlink()
    ons_substations.to_file(gpkg, layer="ons_substations", driver="GPKG")
    ons_lines.to_file(gpkg, layer="ons_transmission_lines", driver="GPKG")


def make_overlay_map(ons_substations: gpd.GeoDataFrame, ons_lines: gpd.GeoDataFrame) -> None:
    gem_buses = gpd.read_file(GRID_DIR / "brazil_power_grid_model.gpkg", layer="buses")
    gem_branches = gpd.read_file(GRID_DIR / "brazil_power_grid_model.gpkg", layer="branches")

    m = folium.Map(location=[-14.2, -51.9], zoom_start=4, tiles="CartoDB positron", control_scale=True)
    folium.TileLayer("CartoDB dark_matter", name="Dark matter").add_to(m)

    ons_line_group = folium.FeatureGroup(name=f"ONS official transmission lines ({len(ons_lines):,})", show=True)
    for row in ons_lines.itertuples():
        coords = [(lat, lon) for lon, lat in row.geometry.coords]
        folium.PolyLine(
            coords,
            color="#111827",
            weight=max(1.0, min(5.5, row.voltage_kv / 120)),
            opacity=0.58,
            tooltip=(
                f"{row.line_name}<br>{row.voltage_kv:.0f} kV<br>"
                f"{row.from_substation} → {row.to_substation}<br>{row.length_km_ons:.1f} km"
            ),
        ).add_to(ons_line_group)
    ons_line_group.add_to(m)

    synthetic_group = folium.FeatureGroup(name=f"Our inferred grid branches ({len(gem_branches):,})", show=False)
    for row in gem_branches.itertuples():
        coords = [(lat, lon) for lon, lat in row.geometry.coords]
        folium.PolyLine(
            coords,
            color="#2563eb",
            weight=max(1.0, min(4.5, row.voltage_kv / 140)),
            opacity=0.38,
            tooltip=f"{row.from_bus} → {row.to_bus}<br>{row.voltage_kv:.0f} kV inferred<br>{row.length_km:.1f} km",
        ).add_to(synthetic_group)
    synthetic_group.add_to(m)

    sub_group = folium.FeatureGroup(name=f"ONS substations ({len(ons_substations):,})", show=False)
    for row in ons_substations.itertuples():
        folium.CircleMarker(
            [row.latitude, row.longitude],
            radius=2.7,
            color="#dc2626",
            fill=True,
            fill_color="#dc2626",
            fill_opacity=0.72,
            weight=0.6,
            tooltip=f"{row.nom_subestacao}<br>{row.val_niveltensao:.0f} kV<br>{row.id_estado} / {row.id_subsistema}",
        ).add_to(sub_group)
    sub_group.add_to(m)

    bus_group = folium.FeatureGroup(name=f"Our inferred buses ({len(gem_buses):,})", show=False)
    for row in gem_buses.itertuples():
        folium.CircleMarker(
            [row.latitude, row.longitude],
            radius=4,
            color="#16a34a",
            fill=True,
            fill_color="#16a34a",
            fill_opacity=0.70,
            weight=0.7,
            tooltip=f"{row.bus_id}<br>{row.dominant_technology}<br>{row.capacity_mw:,.0f} MW",
        ).add_to(bus_group)
    bus_group.add_to(m)

    legend = """
    <div style="position: fixed; bottom: 24px; left: 24px; z-index: 9999; background: white;
      padding: 14px 16px; border: 1px solid #d1d5db; border-radius: 8px;
      box-shadow: 0 10px 25px rgba(15,23,42,.16); font-family: Inter, Arial, sans-serif;
      color: #111827;">
      <div style="font-weight: 800; margin-bottom: 6px;">ONS vs Synthetic Grid</div>
      <div><span style="display:inline-block;width:22px;height:4px;background:#111827;margin-right:8px;"></span>ONS transmission</div>
      <div><span style="display:inline-block;width:22px;height:4px;background:#2563eb;margin-right:8px;"></span>Our inferred branches</div>
      <div><span style="display:inline-block;width:10px;height:10px;background:#dc2626;border-radius:50%;margin-right:8px;"></span>ONS substations</div>
      <div><span style="display:inline-block;width:10px;height:10px;background:#16a34a;border-radius:50%;margin-right:8px;"></span>Our inferred buses</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))
    Fullscreen(position="topright").add_to(m)
    MiniMap(toggle_display=True, minimized=True).add_to(m)
    MeasureControl(position="topleft", primary_length_unit="kilometers").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(OUT_DIR / "ons_vs_synthetic_grid_map.html")


def main() -> None:
    generation_comparison = compare_generation()
    ons_substations, ons_lines = build_ons_network_geometries()
    network_comparison = compare_network(ons_substations, ons_lines)
    dispatch_comparison = compare_ons_dispatch_to_capacity()
    write_geodata(ons_substations, ons_lines)
    make_overlay_map(ons_substations, ons_lines)

    summary = {
        "ons_generation_capacity_mw": float(generation_comparison["installed_mw_ons"].sum()),
        "gem_grid_generation_capacity_mw": float(generation_comparison["installed_mw_gem"].sum()),
        "ons_substations_with_coordinates": int(len(ons_substations)),
        "ons_transmission_lines_with_geometry": int(len(ons_lines)),
        "outputs": {
            "generation_mix": str(OUT_DIR / "generation_mix_ons_vs_gem.csv"),
            "network_topology": str(OUT_DIR / "network_topology_ons_vs_synthetic.csv"),
            "voltage_class": str(OUT_DIR / "voltage_class_ons_vs_synthetic.csv"),
            "dispatch_mix": str(OUT_DIR / "ons_2025_dispatch_mix.csv"),
            "geopackage": str(OUT_DIR / "ons_network_layers.gpkg"),
            "map": str(OUT_DIR / "ons_vs_synthetic_grid_map.html"),
        },
    }
    (OUT_DIR / "ons_comparison_summary.json").write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print("\nGeneration capacity comparison")
    print(generation_comparison.round(2).to_string(index=False))
    print("\nNetwork topology comparison")
    print(network_comparison.round(2).to_string(index=False))
    print("\nONS 2025 dispatch mix")
    print(dispatch_comparison.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
