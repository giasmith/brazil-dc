from __future__ import annotations

import json
import math
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import folium
import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from folium.plugins import Fullscreen, HeatMap, MeasureControl, MiniMap
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import spsolve
from shapely.geometry import LineString


ROOT = Path(__file__).resolve().parents[1]
ONS_DIR = ROOT / "data" / "ons"
GEM_GRID_DIR = ROOT / "clean_data" / "power_grid"
OUT_DIR = ROOT / "clean_data" / "ons_official_grid"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SBASE_MVA = 100.0
TECH_ORDER = ["hydropower", "wind", "solar", "oil_gas", "bioenergy", "coal", "nuclear", "other_thermal", "other"]
THERMAL_TECHS = ["oil_gas", "bioenergy", "coal", "nuclear", "other_thermal", "other"]


def strip_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(ch)
    )


def norm_text(value: object) -> str:
    if pd.isna(value):
        return ""
    text = strip_accents(str(value).upper())
    text = re.sub(r"\b\d{2,4}\s*KV\b", " ", text)
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_voltage_from_connection(value: object) -> float | None:
    if pd.isna(value):
        return None
    match = re.search(r"(\d{2,4})\s*kv", str(value), flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


def clean_blank(series: pd.Series) -> pd.Series:
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
    if "TÉRMICA" in plant_type or "TERM" in plant_type:
        return "other_thermal"
    return "other"


def technology_color(technology: str) -> str:
    return {
        "hydropower": "#2563eb",
        "wind": "#06b6d4",
        "solar": "#f59e0b",
        "oil_gas": "#ef4444",
        "bioenergy": "#16a34a",
        "coal": "#57534e",
        "nuclear": "#7c3aed",
        "other_thermal": "#9ca3af",
        "other": "#64748b",
    }.get(technology, "#64748b")


def line_color(utilization: float) -> str:
    if utilization >= 1:
        return "#dc2626"
    if utilization >= 0.75:
        return "#f97316"
    if utilization >= 0.50:
        return "#eab308"
    return "#111827"


def load_substations() -> gpd.GeoDataFrame:
    sub = pd.read_parquet(ONS_DIR / "subestacao" / "SUBESTACAO.parquet")
    sub["latitude"] = pd.to_numeric(sub["val_latitude"], errors="coerce")
    sub["longitude"] = pd.to_numeric(sub["val_longitude"], errors="coerce")
    sub["voltage_kv"] = pd.to_numeric(sub["val_niveltensao"], errors="coerce")
    sub = sub.dropna(subset=["num_barra", "latitude", "longitude", "voltage_kv"]).copy()
    sub = sub[sub["latitude"].between(-35, 6) & sub["longitude"].between(-75, -25)].copy()
    sub["bus_id"] = "ons_bus_" + sub["num_barra"].astype(int).astype(str)
    sub["substation_norm"] = sub["nom_subestacao"].map(norm_text)
    buses = gpd.GeoDataFrame(
        sub,
        geometry=gpd.points_from_xy(sub["longitude"], sub["latitude"]),
        crs="EPSG:4326",
    )
    buses = buses.sort_values("voltage_kv", ascending=False).drop_duplicates("bus_id")
    return buses.reset_index(drop=True)


def voltage_fallback_limit(voltage_kv: float) -> float:
    if voltage_kv >= 750:
        return 4200.0
    if voltage_kv >= 500:
        return 2300.0
    if voltage_kv >= 345:
        return 1400.0
    if voltage_kv >= 230:
        return 780.0
    return 260.0


def load_official_branches(buses: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    lines = pd.read_parquet(ONS_DIR / "linha_transmissao" / "LINHA_TRANSMISSAO.parquet")
    lines = lines[clean_blank(lines["dat_desativacao"]).eq("")].copy()
    lines = lines.dropna(subset=["num_barra_de", "num_barra_para", "val_niveltensao_kv"]).copy()
    buses_by_bar = buses.set_index(buses["num_barra"].astype(int))

    records = []
    for row in lines.itertuples(index=False):
        from_bar = int(row.num_barra_de)
        to_bar = int(row.num_barra_para)
        if from_bar not in buses_by_bar.index or to_bar not in buses_by_bar.index or from_bar == to_bar:
            continue
        a = buses_by_bar.loc[from_bar]
        b = buses_by_bar.loc[to_bar]
        voltage = float(row.val_niveltensao_kv)
        z_base = voltage**2 / SBASE_MVA
        reactance = pd.to_numeric(row.val_reatancia, errors="coerce")
        length_km = pd.to_numeric(row.val_comprimento, errors="coerce")
        if pd.isna(reactance) or reactance <= 0:
            reactance = max(float(length_km) if pd.notna(length_km) else 10.0, 1.0) * 0.36
        x_pu = max(float(reactance) / z_base, 1e-5)
        limit = pd.to_numeric(row.val_capacoperlongacomlimit, errors="coerce")
        if pd.isna(limit) or limit <= 0:
            limit = pd.to_numeric(row.val_capacoperlongasemlimit, errors="coerce")
        if pd.isna(limit) or limit <= 0:
            limit = voltage_fallback_limit(voltage)

        records.append(
            {
                "branch_id": f"ons_branch_{len(records):05d}",
                "from_bus": f"ons_bus_{from_bar}",
                "to_bus": f"ons_bus_{to_bar}",
                "from_substation": row.nom_subestacao_de,
                "to_substation": row.nom_subestacao_para,
                "line_name": row.nom_linhadetransmissao,
                "owner": row.nom_agenteproprietario,
                "voltage_kv": voltage,
                "length_km": float(length_km) if pd.notna(length_km) else np.nan,
                "resistance_ohm": row.val_resistencia,
                "reactance_ohm": float(reactance),
                "x_pu": x_pu,
                "thermal_limit_mw": float(limit),
                "operation_date": row.dat_entradaoperacao,
                "from_subsystem": row.id_subsistema_terminalde,
                "to_subsystem": row.id_subsistema_terminalpara,
                "from_state": row.id_estado_terminalde,
                "to_state": row.id_estado_terminalpara,
                "geometry": LineString([a.geometry, b.geometry]),
            }
        )
    return gpd.GeoDataFrame(records, crs="EPSG:4326")


def connection_score(connection: str, substation: str) -> float:
    if not connection or not substation:
        return 0.0
    if connection == substation:
        return 1.0
    if connection in substation or substation in connection:
        return 0.92
    c_tokens = set(connection.split())
    s_tokens = set(substation.split())
    if not c_tokens or not s_tokens:
        return 0.0
    overlap = len(c_tokens & s_tokens) / max(len(c_tokens), len(s_tokens))
    sequence = SequenceMatcher(None, connection, substation).ratio()
    return 0.60 * overlap + 0.40 * sequence


def choose_bus_for_generator(row: pd.Series, candidates_by_state: dict[str, pd.DataFrame], candidates_by_subsystem: dict[str, pd.DataFrame], all_buses: pd.DataFrame) -> tuple[str, str, float]:
    state = str(row.get("id_estado", "")).strip()
    subsystem = str(row.get("id_subsistema", "")).strip()
    connection = norm_text(row.get("nom_pontoconexao", ""))
    target_voltage = row.get("connection_voltage_kv")

    candidates = candidates_by_state.get(state)
    if candidates is None or candidates.empty:
        candidates = candidates_by_subsystem.get(subsystem)
    if candidates is None or candidates.empty:
        candidates = all_buses

    best = None
    for cand in candidates.itertuples():
        score = connection_score(connection, cand.substation_norm)
        if pd.notna(target_voltage):
            score -= min(abs(float(target_voltage) - float(cand.voltage_kv)) / 1000.0, 0.25)
        score += min(float(cand.voltage_kv), 765.0) / 10000.0
        if best is None or score > best[0]:
            best = (score, cand.bus_id)

    if best is None:
        fallback = all_buses.iloc[0]
        return fallback.bus_id, "fallback_first_bus", 0.0
    method = "connection_point_match" if best[0] >= 0.38 and connection else "state_subsystem_weighted"
    return best[1], method, float(best[0])


def load_generation_assignments(buses: gpd.GeoDataFrame) -> pd.DataFrame:
    cap = pd.read_parquet(ONS_DIR / "capacidade-geracao" / "CAPACIDADE_GERACAO.parquet")
    cap = cap[clean_blank(cap["dat_desativacao"]).eq("")].copy()
    cap["installed_mw"] = pd.to_numeric(cap["val_potenciaefetiva"], errors="coerce").fillna(0)
    cap = cap[cap["installed_mw"] > 0].copy()
    cap["technology"] = cap.apply(classify_ons, axis=1)

    modality_path = ONS_DIR / "modalidade_usina" / "MODALIDADE_USINA.parquet"
    if modality_path.exists():
        mod = pd.read_parquet(modality_path)
        mod = mod.sort_values("nom_pontoconexao").drop_duplicates("ceg")
        cap = cap.merge(mod[["ceg", "nom_pontoconexao", "id_ons"]], on="ceg", how="left")
    else:
        cap["nom_pontoconexao"] = ""
        cap["id_ons"] = pd.NA

    cap["connection_voltage_kv"] = cap["nom_pontoconexao"].map(parse_voltage_from_connection)

    candidates = buses[["bus_id", "id_estado", "id_subsistema", "nom_subestacao", "substation_norm", "voltage_kv"]].copy()
    candidates_by_state = {k: v for k, v in candidates.groupby("id_estado")}
    candidates_by_subsystem = {k: v for k, v in candidates.groupby("id_subsistema")}

    assignments = cap.apply(
        lambda row: choose_bus_for_generator(row, candidates_by_state, candidates_by_subsystem, candidates),
        axis=1,
        result_type="expand",
    )
    assignments.columns = ["bus_id", "assignment_method", "assignment_score"]
    cap = pd.concat([cap.reset_index(drop=True), assignments], axis=1)
    keep = [
        "bus_id", "assignment_method", "assignment_score", "id_subsistema", "nom_subsistema", "id_estado",
        "nom_estado", "nom_tipousina", "nom_usina", "ceg", "nom_unidadegeradora", "cod_equipamento",
        "nom_combustivel", "nom_pontoconexao", "technology", "installed_mw",
    ]
    return cap[keep]


def subsystem_dispatch_targets() -> pd.DataFrame:
    balance = pd.read_parquet(ONS_DIR / "balanco_energia_subsistema_ho" / "BALANCO_ENERGIA_SUBSISTEMA_2025.parquet")
    balance = balance[~balance["id_subsistema"].eq("SIN")].copy()
    targets = (
        balance.groupby("id_subsistema", as_index=False)
        .agg(
            load_mw=("val_carga", "mean"),
            hydropower=("val_gerhidraulica", "mean"),
            thermal_total=("val_gertermica", "mean"),
            wind=("val_gereolica", "mean"),
            solar=("val_gersolar", "mean"),
            interchange_mw=("val_intercambio", "mean"),
        )
    )
    return targets


def enrich_buses_with_generation_and_load(buses: gpd.GeoDataFrame, generation: pd.DataFrame, branches: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    buses = buses.copy()
    graph = nx.Graph()
    graph.add_nodes_from(buses["bus_id"])
    graph.add_edges_from(branches[["from_bus", "to_bus"]].itertuples(index=False, name=None))
    degree = dict(graph.degree())
    buses["network_degree"] = buses["bus_id"].map(degree).fillna(0).astype(int)

    cap_by_bus_tech = generation.pivot_table(index="bus_id", columns="technology", values="installed_mw", aggfunc="sum", fill_value=0)
    for tech in TECH_ORDER:
        if tech not in cap_by_bus_tech.columns:
            cap_by_bus_tech[tech] = 0.0
    cap_by_bus_tech = cap_by_bus_tech[TECH_ORDER].reset_index()
    buses = buses.merge(cap_by_bus_tech, on="bus_id", how="left")
    for tech in TECH_ORDER:
        buses[tech] = buses[tech].fillna(0.0)
    buses["installed_mw"] = buses[TECH_ORDER].sum(axis=1)

    for col in ["generation_mw", "load_mw"]:
        buses[col] = 0.0
    for tech in TECH_ORDER:
        buses[f"dispatch_{tech}_mw"] = 0.0

    targets = subsystem_dispatch_targets()
    for target in targets.itertuples():
        mask = buses["id_subsistema"].eq(target.id_subsistema)
        sub = buses[mask].copy()
        if sub.empty:
            continue

        load_weight = (sub["network_degree"] + 1) * np.sqrt(sub["voltage_kv"].clip(lower=1))
        load_weight = load_weight / load_weight.sum()
        buses.loc[mask, "load_mw"] = target.load_mw * load_weight.to_numpy()

        tech_targets = {
            "hydropower": target.hydropower,
            "wind": target.wind,
            "solar": target.solar,
        }
        thermal_capacity = sub[THERMAL_TECHS].sum()
        if thermal_capacity.sum() > 0:
            for tech in THERMAL_TECHS:
                tech_targets[tech] = target.thermal_total * (thermal_capacity[tech] / thermal_capacity.sum())

        for tech, value in tech_targets.items():
            weights = sub[tech].clip(lower=0)
            if weights.sum() == 0:
                weights = (sub["network_degree"] + 1) * np.sqrt(sub["voltage_kv"].clip(lower=1))
            weights = weights / weights.sum()
            dispatch = value * weights.to_numpy()
            buses.loc[mask, f"dispatch_{tech}_mw"] = dispatch
            buses.loc[mask, "generation_mw"] += dispatch

    buses["net_injection_mw"] = buses["generation_mw"] - buses["load_mw"]
    buses["dominant_technology"] = buses[TECH_ORDER].idxmax(axis=1)
    buses.loc[buses["installed_mw"].eq(0), "dominant_technology"] = "load_only"
    return buses


def solve_dc_components(buses: gpd.GeoDataFrame, branches: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict]:
    buses = buses.copy().reset_index(drop=True)
    branches = branches.copy().reset_index(drop=True)
    bus_index = {bus_id: i for i, bus_id in enumerate(buses["bus_id"])}
    graph = nx.Graph()
    graph.add_nodes_from(buses["bus_id"])
    for row in branches.itertuples():
        graph.add_edge(row.from_bus, row.to_bus, branch_index=row.Index)

    buses["component_id"] = -1
    buses["voltage_angle_rad"] = 0.0
    branches["flow_mw"] = 0.0
    component_summaries = []

    for component_id, component_nodes in enumerate(nx.connected_components(graph)):
        nodes = sorted(component_nodes)
        node_set = set(nodes)
        component_branch_idx = [
            i for i, row in branches.iterrows()
            if row["from_bus"] in node_set and row["to_bus"] in node_set
        ]
        buses.loc[buses["bus_id"].isin(node_set), "component_id"] = component_id
        if len(nodes) < 2 or not component_branch_idx:
            continue

        local_index = {bus_id: i for i, bus_id in enumerate(nodes)}
        rows, cols, data = [], [], []
        for bi in component_branch_idx:
            branch = branches.loc[bi]
            i = local_index[branch["from_bus"]]
            j = local_index[branch["to_bus"]]
            susceptance = 1.0 / max(branch["x_pu"], 1e-5)
            rows.extend([i, j, i, j])
            cols.extend([i, j, j, i])
            data.extend([susceptance, susceptance, -susceptance, -susceptance])

        bmat = csr_matrix((data, (rows, cols)), shape=(len(nodes), len(nodes)))
        p = buses.set_index("bus_id").loc[nodes, "net_injection_mw"].to_numpy() / SBASE_MVA
        imbalance_before = float(p.sum() * SBASE_MVA)
        p = p - p.mean()
        slack_local = int(np.argmax(buses.set_index("bus_id").loc[nodes, "generation_mw"].to_numpy()))
        keep = np.array([i for i in range(len(nodes)) if i != slack_local])
        theta = np.zeros(len(nodes))
        try:
            theta[keep] = spsolve(bmat[keep][:, keep], p[keep])
        except Exception:
            theta[:] = 0

        for bus_id, angle in zip(nodes, theta):
            buses.loc[buses["bus_id"].eq(bus_id), "voltage_angle_rad"] = angle

        for bi in component_branch_idx:
            branch = branches.loc[bi]
            i = local_index[branch["from_bus"]]
            j = local_index[branch["to_bus"]]
            branches.loc[bi, "flow_mw"] = (theta[i] - theta[j]) / branch["x_pu"] * SBASE_MVA

        component_summaries.append(
            {
                "component_id": component_id,
                "bus_count": len(nodes),
                "branch_count": len(component_branch_idx),
                "pre_balance_imbalance_mw": imbalance_before,
            }
        )

    branches["abs_flow_mw"] = branches["flow_mw"].abs()
    branches["utilization"] = branches["abs_flow_mw"] / branches["thermal_limit_mw"].replace(0, np.nan)
    branches["utilization"] = branches["utilization"].fillna(0)
    branches["overloaded"] = branches["utilization"] > 1.0
    buses["voltage_angle_deg"] = np.degrees(buses["voltage_angle_rad"])

    summary = {
        "component_count": int(nx.number_connected_components(graph)),
        "largest_component_buses": int(max((len(c) for c in nx.connected_components(graph)), default=0)),
        "component_balancing_note": "DC solves were run per connected component after removing each component mean injection.",
        "components": component_summaries,
    }
    return buses, branches, summary


def write_outputs(buses: gpd.GeoDataFrame, branches: gpd.GeoDataFrame, generation: pd.DataFrame, summary: dict) -> None:
    buses.drop(columns="geometry").to_csv(OUT_DIR / "ons_official_grid_buses.csv", index=False)
    branches.drop(columns="geometry").to_csv(OUT_DIR / "ons_official_grid_branches.csv", index=False)
    generation.to_csv(OUT_DIR / "ons_clean_generation_assignments.csv", index=False)

    gpkg = OUT_DIR / "ons_official_grid_model.gpkg"
    if gpkg.exists():
        gpkg.unlink()
    buses.to_file(gpkg, layer="buses", driver="GPKG")
    branches.to_file(gpkg, layer="branches", driver="GPKG")

    (OUT_DIR / "ons_official_grid_summary.json").write_text(json.dumps(summary, indent=2))


def compare_energy_mix(generation: pd.DataFrame) -> pd.DataFrame:
    ons = (
        generation.groupby("technology", as_index=False)
        .agg(ons_units=("cod_equipamento", "count"), installed_mw_ons=("installed_mw", "sum"))
    )
    ons["ons_share_pct"] = ons["installed_mw_ons"] / ons["installed_mw_ons"].sum() * 100

    gem = pd.read_csv(GEM_GRID_DIR / "brazil_power_grid_generators.csv")
    gem_mix = (
        gem.groupby("technology", as_index=False)
        .agg(gem_assets=("generator_id", "count"), installed_mw_gem=("capacity_mw", "sum"), modeled_available_mw_gem=("dispatchable_mw", "sum"))
    )
    gem_mix["gem_share_pct"] = gem_mix["installed_mw_gem"] / gem_mix["installed_mw_gem"].sum() * 100

    comparison = ons.merge(gem_mix, on="technology", how="outer").fillna(0)
    comparison["installed_mw_delta_gem_minus_ons"] = comparison["installed_mw_gem"] - comparison["installed_mw_ons"]
    comparison["installed_pct_delta_gem_minus_ons"] = comparison["gem_share_pct"] - comparison["ons_share_pct"]
    comparison = comparison.sort_values("installed_mw_ons", ascending=False)
    comparison.to_csv(OUT_DIR / "energy_mix_ons_vs_gem.csv", index=False)
    return comparison


def make_map(buses: gpd.GeoDataFrame, branches: gpd.GeoDataFrame, generation: pd.DataFrame, comparison: pd.DataFrame) -> None:
    m = folium.Map(location=[-14.2, -51.9], zoom_start=4, tiles="CartoDB positron", control_scale=True, prefer_canvas=True)
    folium.TileLayer("CartoDB dark_matter", name="Dark matter").add_to(m)

    branch_group = folium.FeatureGroup(name=f"ONS official transmission branches ({len(branches):,})", show=True)
    for row in branches.itertuples():
        coords = [(lat, lon) for lon, lat in row.geometry.coords]
        folium.PolyLine(
            coords,
            color=line_color(row.utilization),
            weight=max(1.0, min(6.5, row.voltage_kv / 120)),
            opacity=0.62,
            tooltip=(
                f"{row.line_name}<br>{row.voltage_kv:.0f} kV<br>"
                f"{row.from_substation} → {row.to_substation}<br>"
                f"Flow: {row.flow_mw:,.1f} MW<br>Utilization: {row.utilization:.1%}<br>"
                f"Limit: {row.thermal_limit_mw:,.0f} MW"
            ),
        ).add_to(branch_group)
    branch_group.add_to(m)

    bus_group = folium.FeatureGroup(name=f"ONS topology buses ({len(buses):,})", show=True)
    max_installed = max(buses["installed_mw"].max(), 1)
    for row in buses.itertuples():
        radius = 2.5 + 9 * math.sqrt(max(row.installed_mw, 0) / max_installed)
        fill = "#16a34a" if row.net_injection_mw >= 0 else "#7c3aed"
        folium.CircleMarker(
            location=[row.latitude, row.longitude],
            radius=radius,
            color=technology_color(row.dominant_technology),
            weight=1,
            fill=True,
            fill_color=fill,
            fill_opacity=0.72,
            tooltip=(
                f"{row.nom_subestacao}<br>{row.id_estado} / {row.id_subsistema}<br>"
                f"{row.voltage_kv:.0f} kV<br>Dominant: {row.dominant_technology}<br>"
                f"Installed: {row.installed_mw:,.1f} MW<br>"
                f"Dispatch: {row.generation_mw:,.1f} MW<br>"
                f"Load: {row.load_mw:,.1f} MW<br>"
                f"Net: {row.net_injection_mw:,.1f} MW"
            ),
        ).add_to(bus_group)
    bus_group.add_to(m)

    heat = buses[buses["installed_mw"] > 0][["latitude", "longitude", "installed_mw"]].values.tolist()
    HeatMap(heat, name="ONS installed capacity heatmap", radius=12, blur=15, show=False).add_to(m)

    legend = f"""
    <div style="position: fixed; bottom: 24px; left: 24px; z-index: 9999; background: white;
      padding: 14px 16px; border: 1px solid #d1d5db; border-radius: 8px;
      box-shadow: 0 10px 25px rgba(15,23,42,.16); font-family: Inter, Arial, sans-serif;
      color: #111827; max-width: 360px;">
      <div style="font-weight: 800; margin-bottom: 6px;">ONS Official Physics Grid</div>
      <div style="font-size: 12px; line-height: 1.35; margin-bottom: 8px;">
        {len(buses):,} ONS buses, {len(branches):,} official branches,
        {generation['installed_mw'].sum():,.0f} MW official installed capacity.
      </div>
      <div><span style="display:inline-block;width:22px;height:4px;background:#111827;margin-right:8px;"></span>&lt;50% branch loading</div>
      <div><span style="display:inline-block;width:22px;height:4px;background:#eab308;margin-right:8px;"></span>50-75%</div>
      <div><span style="display:inline-block;width:22px;height:4px;background:#f97316;margin-right:8px;"></span>75-100%</div>
      <div><span style="display:inline-block;width:22px;height:4px;background:#dc2626;margin-right:8px;"></span>Overloaded</div>
      <div style="margin-top: 7px;"><span style="display:inline-block;width:10px;height:10px;background:#16a34a;border-radius:50%;margin-right:8px;"></span>Net exporter bus</div>
      <div><span style="display:inline-block;width:10px;height:10px;background:#7c3aed;border-radius:50%;margin-right:8px;"></span>Net importer bus</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))
    Fullscreen(position="topright").add_to(m)
    MiniMap(toggle_display=True, minimized=True).add_to(m)
    MeasureControl(position="topleft", primary_length_unit="kilometers").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(OUT_DIR / "ons_official_grid_interactive_map.html")


def main() -> None:
    buses = load_substations()
    branches = load_official_branches(buses)
    generation = load_generation_assignments(buses)
    buses = enrich_buses_with_generation_and_load(buses, generation, branches)
    buses, branches, solve_summary = solve_dc_components(buses, branches)
    comparison = compare_energy_mix(generation)

    summary = {
        "source": "ONS AWS Open Data",
        "bus_count": int(len(buses)),
        "branch_count": int(len(branches)),
        "generation_unit_count": int(len(generation)),
        "installed_capacity_mw": float(generation["installed_mw"].sum()),
        "modeled_generation_mw": float(buses["generation_mw"].sum()),
        "modeled_load_mw": float(buses["load_mw"].sum()),
        "max_branch_utilization": float(branches["utilization"].max()),
        "overloaded_branch_count": int(branches["overloaded"].sum()),
        "generation_assignment": {
            "connection_point_match": int(generation["assignment_method"].eq("connection_point_match").sum()),
            "state_subsystem_weighted": int(generation["assignment_method"].eq("state_subsystem_weighted").sum()),
            "fallback_first_bus": int(generation["assignment_method"].eq("fallback_first_bus").sum()),
            "note": "ONS capacity data has official plant/unit capacity but no direct bus coordinate. MODALIDADE_USINA connection points are matched to ONS substations where possible; otherwise capacity is assigned within state/subsystem.",
        },
        **solve_summary,
    }
    write_outputs(buses, branches, generation, summary)
    make_map(buses, branches, generation, comparison)

    print(json.dumps(summary, indent=2))
    print("\nEnergy mix comparison")
    print(comparison.round(2).to_string(index=False))
    print(f"\nOutputs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
