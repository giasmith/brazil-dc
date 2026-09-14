from __future__ import annotations

import json
import math
from pathlib import Path

import folium
import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from folium.plugins import Fullscreen, HeatMap, MeasureControl, MiniMap
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import spsolve
from scipy.spatial import cKDTree
from shapely.geometry import LineString


ROOT = Path(__file__).resolve().parents[1]
ENERGY_DIR = ROOT / "clean_data" / "energy"
ONS_GRID_DIR = ROOT / "clean_data" / "ons_official_grid"
OUT_DIR = ROOT / "clean_data" / "gem_proposed_overlay"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SBASE_MVA = 100.0
PROPOSED_STATUSES = {"announced", "pre-construction", "construction"}
TECH_ASSUMPTIONS = {
    "solar": 0.24,
    "wind": 0.46,
    "hydropower": 0.58,
    "bioenergy": 0.72,
    "oil_gas": 0.50,
    "coal": 0.62,
    "nuclear": 0.90,
}

STATE_ALIASES = {
    "Acre": "AC",
    "Alagoas": "AL",
    "Amapá": "AP",
    "Amazonas": "AM",
    "Bahia": "BA",
    "Ceará": "CE",
    "Distrito Federal": "DF",
    "Espirito Santo": "ES",
    "Espírito Santo": "ES",
    "Goiás": "GO",
    "Maranhão": "MA",
    "Mato Grosso": "MT",
    "Mato Grosso do Sul": "MS",
    "Minas Gerais": "MG",
    "Pará": "PA",
    "Paraíba": "PB",
    "Paraná": "PR",
    "Pernambuco": "PE",
    "Piauí": "PI",
    "Rio de Janeiro": "RJ",
    "Rio Grande do Norte": "RN",
    "Rio Grande do Sul": "RS",
    "Rondônia": "RO",
    "Roraima": "RR",
    "Santa Catarina": "SC",
    "São Paulo": "SP",
    "Sergipe": "SE",
    "Tocantins": "TO",
}


SOURCES = [
    ("solar.csv", "solar", ("country/area",), "project_name", "phase_name", "capacity_(mw)", "status", "state/province", "latitude", "longitude"),
    ("wind.csv", "wind", ("country/area",), "project_name", "phase_name", "capacity_(mw)", "status", "state/province", "latitude", "longitude"),
    ("hydropower.csv", "hydropower", ("country/area_1", "country/area_2"), "project_name", None, "capacity_(mw)", "status", "state/province_1", "latitude", "longitude"),
    ("bioenergy.csv", "bioenergy", ("country/area",), "project_name", "unit_name", "capacity_(mw)", "status", "state/province", "latitude", "longitude"),
    ("oil_and_gas_plants.csv", "oil_gas", ("country/area",), "plant_name", "unit_name", "capacity_(mw)", "status", "state/province", "latitude", "longitude"),
    ("coal_plants.csv", "coal", ("country/area",), "plant_name", "unit_name", "capacity_(mw)", "status", "subnational_unit_(province,_state)", "latitude", "longitude"),
    ("nuclear.csv", "nuclear", ("country/area",), "project_name", "unit_name", "capacity_(mw)", "status", "state/province", "latitude", "longitude"),
]


def clean_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("--", "", regex=False)
        .str.replace("*", "", regex=False)
        .str.strip(),
        errors="coerce",
    )


def normalize_state(value: object) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return STATE_ALIASES.get(text)


def has_brazil(row: pd.Series, columns: tuple[str, ...]) -> bool:
    return any("brazil" in str(row.get(column, "")).lower() for column in columns)


def load_proposed_generators() -> gpd.GeoDataFrame:
    frames = []
    for file_name, technology, country_cols, name_col, unit_col, cap_col, status_col, state_col, lat_col, lon_col in SOURCES:
        df = pd.read_csv(ENERGY_DIR / file_name, low_memory=False)
        df = df[df.apply(lambda row: has_brazil(row, country_cols), axis=1)].copy()
        df["status_norm"] = df[status_col].fillna("").astype(str).str.lower().str.strip()
        df = df[df["status_norm"].isin(PROPOSED_STATUSES)].copy()
        df["capacity_mw"] = clean_number(df[cap_col])
        df["latitude"] = clean_number(df[lat_col])
        df["longitude"] = clean_number(df[lon_col])
        df = df.dropna(subset=["capacity_mw", "latitude", "longitude"])
        df = df[df["capacity_mw"] > 0].copy()
        df = df[df["latitude"].between(-35, 6) & df["longitude"].between(-75, -25)].copy()
        if df.empty:
            continue

        base_name = df[name_col].fillna("Unnamed proposed asset").astype(str)
        if unit_col and unit_col in df.columns:
            unit = df[unit_col].fillna("").astype(str)
            df["asset_name"] = np.where(unit.str.strip().isin(["", "--", "nan"]), base_name, base_name + " - " + unit)
        else:
            df["asset_name"] = base_name
        df["technology"] = technology
        df["state_code"] = df[state_col].map(normalize_state) if state_col else None
        df["source_file"] = file_name
        df["capacity_factor"] = TECH_ASSUMPTIONS[technology]
        df["proposed_dispatch_mw"] = df["capacity_mw"] * df["capacity_factor"]
        frames.append(df[[
            "asset_name", "technology", "status_norm", "capacity_mw", "capacity_factor",
            "proposed_dispatch_mw", "state_code", "latitude", "longitude", "source_file",
        ]])

    proposed = pd.concat(frames, ignore_index=True)
    proposed["proposed_id"] = [f"gem_prop_{i:05d}" for i in range(len(proposed))]
    return gpd.GeoDataFrame(
        proposed,
        geometry=gpd.points_from_xy(proposed["longitude"], proposed["latitude"]),
        crs="EPSG:4326",
    )


def haversine_km(lon1, lat1, lon2, lat2) -> float:
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def assign_to_ons_buses(proposed: gpd.GeoDataFrame, buses: pd.DataFrame) -> gpd.GeoDataFrame:
    proposed = proposed.copy()
    buses = buses.copy()
    trees = {}
    for state, sub in buses.groupby("id_estado"):
        coords = sub[["longitude", "latitude"]].to_numpy()
        trees[state] = (cKDTree(coords), sub.reset_index(drop=True))
    all_tree = cKDTree(buses[["longitude", "latitude"]].to_numpy())
    all_buses = buses.reset_index(drop=True)

    assigned = []
    for row in proposed.itertuples():
        if row.state_code in trees:
            tree, candidates = trees[row.state_code]
            _, idx = tree.query([row.longitude, row.latitude], k=1)
            bus = candidates.iloc[int(idx)]
            method = "nearest_same_state_ons_bus"
        else:
            _, idx = all_tree.query([row.longitude, row.latitude], k=1)
            bus = all_buses.iloc[int(idx)]
            method = "nearest_ons_bus"
        distance = haversine_km(row.longitude, row.latitude, bus.longitude, bus.latitude)
        assigned.append((bus.bus_id, bus.nom_subestacao, bus.id_subsistema, bus.id_estado, bus.voltage_kv, distance, method))

    cols = ["assigned_bus", "assigned_substation", "assigned_subsystem", "assigned_state", "assigned_voltage_kv", "distance_to_bus_km", "assignment_method"]
    proposed[cols] = pd.DataFrame(assigned, index=proposed.index)
    return proposed


def connected_components(branches: pd.DataFrame, buses: pd.DataFrame) -> dict[str, int]:
    graph = nx.Graph()
    graph.add_nodes_from(buses["bus_id"])
    graph.add_edges_from(branches[["from_bus", "to_bus"]].itertuples(index=False, name=None))
    mapping = {}
    for component_id, nodes in enumerate(nx.connected_components(graph)):
        for node in nodes:
            mapping[node] = component_id
    return mapping


def solve_dc(buses: pd.DataFrame, branches: pd.DataFrame, injection_col: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    buses = buses.copy().reset_index(drop=True)
    branches = branches.copy().reset_index(drop=True)
    graph = nx.Graph()
    graph.add_nodes_from(buses["bus_id"])
    for idx, row in branches.iterrows():
        graph.add_edge(row["from_bus"], row["to_bus"], branch_index=idx)

    buses[f"{injection_col}_angle_rad"] = 0.0
    branches[f"{injection_col}_flow_mw"] = 0.0

    for nodes in nx.connected_components(graph):
        nodes = sorted(nodes)
        if len(nodes) < 2:
            continue
        node_set = set(nodes)
        branch_idx = [i for i, row in branches.iterrows() if row["from_bus"] in node_set and row["to_bus"] in node_set]
        if not branch_idx:
            continue
        local_index = {bus_id: i for i, bus_id in enumerate(nodes)}
        rows, cols, data = [], [], []
        for bi in branch_idx:
            br = branches.loc[bi]
            i = local_index[br["from_bus"]]
            j = local_index[br["to_bus"]]
            susceptance = 1.0 / max(br["x_pu"], 1e-5)
            rows.extend([i, j, i, j])
            cols.extend([i, j, j, i])
            data.extend([susceptance, susceptance, -susceptance, -susceptance])
        bmat = csr_matrix((data, (rows, cols)), shape=(len(nodes), len(nodes)))
        p = buses.set_index("bus_id").loc[nodes, injection_col].to_numpy() / SBASE_MVA
        p = p - p.mean()
        slack_local = int(np.argmax(buses.set_index("bus_id").loc[nodes, "generation_mw"].to_numpy()))
        keep = np.array([i for i in range(len(nodes)) if i != slack_local])
        theta = np.zeros(len(nodes))
        theta[keep] = spsolve(bmat[keep][:, keep], p[keep])
        for bus_id, angle in zip(nodes, theta):
            buses.loc[buses["bus_id"].eq(bus_id), f"{injection_col}_angle_rad"] = angle
        for bi in branch_idx:
            br = branches.loc[bi]
            i = local_index[br["from_bus"]]
            j = local_index[br["to_bus"]]
            branches.loc[bi, f"{injection_col}_flow_mw"] = (theta[i] - theta[j]) / br["x_pu"] * SBASE_MVA

    branches[f"{injection_col}_abs_flow_mw"] = branches[f"{injection_col}_flow_mw"].abs()
    branches[f"{injection_col}_utilization"] = branches[f"{injection_col}_abs_flow_mw"] / branches["thermal_limit_mw"].replace(0, np.nan)
    branches[f"{injection_col}_utilization"] = branches[f"{injection_col}_utilization"].fillna(0)
    branches[f"{injection_col}_overload_mw"] = (branches[f"{injection_col}_abs_flow_mw"] - branches["thermal_limit_mw"]).clip(lower=0)
    branches[f"{injection_col}_overloaded"] = branches[f"{injection_col}_utilization"] > 1
    return buses, branches


def line_color(utilization: float) -> str:
    if utilization >= 1:
        return "#dc2626"
    if utilization >= 0.75:
        return "#f97316"
    if utilization >= 0.50:
        return "#eab308"
    return "#111827"


def tech_color(technology: str) -> str:
    return {
        "solar": "#f59e0b",
        "wind": "#06b6d4",
        "hydropower": "#2563eb",
        "bioenergy": "#16a34a",
        "oil_gas": "#ef4444",
        "coal": "#57534e",
        "nuclear": "#7c3aed",
    }.get(technology, "#64748b")


def make_map(proposed: gpd.GeoDataFrame, buses: gpd.GeoDataFrame, branches: gpd.GeoDataFrame, summary: dict) -> None:
    m = folium.Map(location=[-14.2, -51.9], zoom_start=4, tiles="CartoDB positron", control_scale=True, prefer_canvas=True)
    folium.TileLayer("CartoDB dark_matter", name="Dark matter").add_to(m)

    branch_group = folium.FeatureGroup(name="ONS branches: with GEM proposed dispatch", show=True)
    for row in branches.itertuples():
        coords = [(lat, lon) for lon, lat in row.geometry.coords]
        util = row.scenario_net_injection_mw_utilization
        base_util = row.base_net_injection_mw_utilization
        folium.PolyLine(
            coords,
            color=line_color(util),
            weight=max(1.0, min(7.0, row.voltage_kv / 115)),
            opacity=0.68,
            tooltip=(
                f"{row.line_name}<br>{row.voltage_kv:.0f} kV<br>"
                f"{row.from_substation} → {row.to_substation}<br>"
                f"Baseline util: {base_util:.1%}<br>"
                f"Scenario util: {util:.1%}<br>"
                f"Delta flow: {row.delta_abs_flow_mw:,.1f} MW"
            ),
        ).add_to(branch_group)
    branch_group.add_to(m)

    stressed = branches[branches["scenario_net_injection_mw_utilization"].ge(0.75)].copy()
    stress_group = folium.FeatureGroup(name=f"Stressed branches >=75% ({len(stressed):,})", show=True)
    for row in stressed.itertuples():
        coords = [(lat, lon) for lon, lat in row.geometry.coords]
        folium.PolyLine(
            coords,
            color=line_color(row.scenario_net_injection_mw_utilization),
            weight=5.5,
            opacity=0.92,
            tooltip=f"{row.line_name}<br>{row.scenario_net_injection_mw_utilization:.1%} utilization",
        ).add_to(stress_group)
    stress_group.add_to(m)

    prop_group = folium.FeatureGroup(name=f"GEM proposed generators ({len(proposed):,})", show=True)
    max_cap = max(proposed["capacity_mw"].max(), 1)
    for row in proposed.itertuples():
        folium.CircleMarker(
            [row.latitude, row.longitude],
            radius=2.2 + 7 * math.sqrt(row.capacity_mw / max_cap),
            color=tech_color(row.technology),
            weight=0.8,
            fill=True,
            fill_color=tech_color(row.technology),
            fill_opacity=0.78,
            tooltip=(
                f"{row.asset_name}<br>{row.technology} / {row.status_norm}<br>"
                f"{row.capacity_mw:,.1f} MW installed<br>{row.proposed_dispatch_mw:,.1f} MW modeled dispatch<br>"
                f"Assigned to {row.assigned_substation} ({row.distance_to_bus_km:.1f} km)"
            ),
        ).add_to(prop_group)
    prop_group.add_to(m)

    HeatMap(
        proposed[["latitude", "longitude", "proposed_dispatch_mw"]].values.tolist(),
        name="Proposed dispatch heatmap",
        radius=12,
        blur=16,
        show=False,
    ).add_to(m)

    legend = f"""
    <div style="position: fixed; bottom: 24px; left: 24px; z-index: 9999; background: white;
      padding: 14px 16px; border: 1px solid #d1d5db; border-radius: 8px;
      box-shadow: 0 10px 25px rgba(15,23,42,.16); font-family: Inter, Arial, sans-serif;
      color: #111827; max-width: 380px;">
      <div style="font-weight: 800; margin-bottom: 6px;">GEM Proposed on ONS Topology</div>
      <div style="font-size: 12px; line-height: 1.35; margin-bottom: 8px;">
        {summary['proposed_generator_count']:,} proposed GEM assets,
        {summary['proposed_capacity_mw']:,.0f} MW installed,
        {summary['proposed_dispatch_mw']:,.0f} MW modeled dispatch.
      </div>
      <div><span style="display:inline-block;width:22px;height:4px;background:#111827;margin-right:8px;"></span>&lt;50% line loading</div>
      <div><span style="display:inline-block;width:22px;height:4px;background:#eab308;margin-right:8px;"></span>50-75%</div>
      <div><span style="display:inline-block;width:22px;height:4px;background:#f97316;margin-right:8px;"></span>75-100%</div>
      <div><span style="display:inline-block;width:22px;height:4px;background:#dc2626;margin-right:8px;"></span>Overloaded</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))
    Fullscreen(position="topright").add_to(m)
    MiniMap(toggle_display=True, minimized=True).add_to(m)
    MeasureControl(position="topleft", primary_length_unit="kilometers").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(OUT_DIR / "gem_proposed_on_ons_topology_map.html")


def main() -> None:
    buses = gpd.read_file(ONS_GRID_DIR / "ons_official_grid_model.gpkg", layer="buses")
    branches = gpd.read_file(ONS_GRID_DIR / "ons_official_grid_model.gpkg", layer="branches")
    proposed = assign_to_ons_buses(load_proposed_generators(), buses.drop(columns="geometry"))

    proposed_by_bus = proposed.groupby("assigned_bus", as_index=False).agg(
        proposed_capacity_mw=("capacity_mw", "sum"),
        proposed_dispatch_mw=("proposed_dispatch_mw", "sum"),
        proposed_generator_count=("proposed_id", "count"),
    )
    buses = buses.merge(proposed_by_bus, left_on="bus_id", right_on="assigned_bus", how="left")
    buses[["proposed_capacity_mw", "proposed_dispatch_mw", "proposed_generator_count"]] = buses[["proposed_capacity_mw", "proposed_dispatch_mw", "proposed_generator_count"]].fillna(0)
    buses["base_net_injection_mw"] = buses["generation_mw"] - buses["load_mw"]
    buses["scenario_net_injection_mw"] = buses["base_net_injection_mw"] + buses["proposed_dispatch_mw"]

    bus_components = connected_components(branches, buses)
    buses["component_id"] = buses["bus_id"].map(bus_components)
    proposed["component_id"] = proposed["assigned_bus"].map(bus_components)

    buses, branches = solve_dc(buses, branches, "base_net_injection_mw")
    buses, branches = solve_dc(buses, branches, "scenario_net_injection_mw")
    branches["delta_abs_flow_mw"] = branches["scenario_net_injection_mw_abs_flow_mw"] - branches["base_net_injection_mw_abs_flow_mw"]
    branches["delta_utilization"] = branches["scenario_net_injection_mw_utilization"] - branches["base_net_injection_mw_utilization"]

    stressed_components = set(branches.loc[branches["scenario_net_injection_mw_utilization"].ge(0.75), "from_bus"].map(bus_components).dropna())
    overloaded_components = set(branches.loc[branches["scenario_net_injection_mw_overloaded"], "from_bus"].map(bus_components).dropna())
    proposed_exposed_75 = proposed[proposed["component_id"].isin(stressed_components)]["proposed_dispatch_mw"].sum()
    proposed_exposed_overload = proposed[proposed["component_id"].isin(overloaded_components)]["proposed_dispatch_mw"].sum()

    mix = proposed.groupby(["technology", "status_norm"], as_index=False).agg(
        proposed_assets=("proposed_id", "count"),
        proposed_capacity_mw=("capacity_mw", "sum"),
        proposed_dispatch_mw=("proposed_dispatch_mw", "sum"),
    ).sort_values("proposed_capacity_mw", ascending=False)
    state_mix = proposed.groupby(["assigned_subsystem", "assigned_state", "technology"], as_index=False).agg(
        proposed_assets=("proposed_id", "count"),
        proposed_capacity_mw=("capacity_mw", "sum"),
        proposed_dispatch_mw=("proposed_dispatch_mw", "sum"),
    ).sort_values("proposed_dispatch_mw", ascending=False)

    summary = {
        "definition": "GEM proposed assets are statuses announced, pre-construction, and construction.",
        "proposed_generator_count": int(len(proposed)),
        "proposed_capacity_mw": float(proposed["capacity_mw"].sum()),
        "proposed_dispatch_mw": float(proposed["proposed_dispatch_mw"].sum()),
        "baseline_overloaded_branch_count": int(branches["base_net_injection_mw_overloaded"].sum()),
        "scenario_overloaded_branch_count": int(branches["scenario_net_injection_mw_overloaded"].sum()),
        "baseline_branches_over_75pct": int(branches["base_net_injection_mw_utilization"].ge(0.75).sum()),
        "scenario_branches_over_75pct": int(branches["scenario_net_injection_mw_utilization"].ge(0.75).sum()),
        "baseline_total_overload_mw": float(branches["base_net_injection_mw_overload_mw"].sum()),
        "scenario_total_overload_mw": float(branches["scenario_net_injection_mw_overload_mw"].sum()),
        "incremental_overload_mw": float(branches["scenario_net_injection_mw_overload_mw"].sum() - branches["base_net_injection_mw_overload_mw"].sum()),
        "max_baseline_utilization": float(branches["base_net_injection_mw_utilization"].max()),
        "max_scenario_utilization": float(branches["scenario_net_injection_mw_utilization"].max()),
        "proposed_dispatch_in_components_with_75pct_branch_mw": float(proposed_exposed_75),
        "proposed_dispatch_in_components_with_overload_mw": float(proposed_exposed_overload),
        "method_notes": [
            "The ONS topology is held fixed; no new transmission is added.",
            "GEM proposed generators are assigned to the nearest ONS bus, preferring the same state when possible.",
            "Scenario dispatch uses technology capacity-factor assumptions, then a DC power-flow solve by connected component.",
            "Incremental overload MW is a transmission stress proxy, not a full optimal-power-flow curtailment estimate.",
        ],
    }

    proposed.to_file(OUT_DIR / "gem_proposed_generators.geojson", driver="GeoJSON")
    buses.to_file(OUT_DIR / "gem_proposed_overlay_model.gpkg", layer="buses", driver="GPKG")
    branches.to_file(OUT_DIR / "gem_proposed_overlay_model.gpkg", layer="branches", driver="GPKG")
    proposed.to_file(OUT_DIR / "gem_proposed_overlay_model.gpkg", layer="proposed_generators", driver="GPKG")
    proposed.drop(columns="geometry").to_csv(OUT_DIR / "gem_proposed_generators_assigned.csv", index=False)
    buses.drop(columns="geometry").to_csv(OUT_DIR / "ons_buses_with_gem_proposed.csv", index=False)
    branches.drop(columns="geometry").to_csv(OUT_DIR / "ons_branches_gem_proposed_scenario.csv", index=False)
    mix.to_csv(OUT_DIR / "gem_proposed_mix.csv", index=False)
    state_mix.to_csv(OUT_DIR / "gem_proposed_state_mix.csv", index=False)
    branches.sort_values("scenario_net_injection_mw_utilization", ascending=False).head(50).drop(columns="geometry").to_csv(OUT_DIR / "top_stressed_branches.csv", index=False)
    (OUT_DIR / "gem_proposed_overlay_summary.json").write_text(json.dumps(summary, indent=2))

    make_map(proposed, buses, branches, summary)

    print(json.dumps(summary, indent=2))
    print("\nProposed mix")
    print(mix.round(2).to_string(index=False))
    print("\nTop stressed branches")
    print(branches.sort_values("scenario_net_injection_mw_utilization", ascending=False)[[
        "line_name", "from_substation", "to_substation", "voltage_kv",
        "base_net_injection_mw_utilization", "scenario_net_injection_mw_utilization",
        "scenario_net_injection_mw_overload_mw", "delta_abs_flow_mw",
    ]].head(12).round(3).to_string(index=False))
    print(f"\nOutputs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
