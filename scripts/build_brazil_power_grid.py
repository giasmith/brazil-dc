from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import folium
import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from folium.plugins import Fullscreen, HeatMap, MeasureControl, MiniMap
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import spsolve
from scipy.spatial import Delaunay, cKDTree
from shapely.geometry import LineString, Point
from shapely import wkt


ROOT = Path(__file__).resolve().parents[1]
ENERGY_DIR = ROOT / "clean_data" / "energy"
OUT_DIR = ROOT / "clean_data" / "power_grid"
OUT_DIR.mkdir(parents=True, exist_ok=True)

ACTIVE_STATUSES = {
    "operating",
    "construction",
}

TECH_ASSUMPTIONS = {
    "solar": {"capacity_factor": 0.24, "ramp_score": 0.35, "voltage_bias": 0.90},
    "wind": {"capacity_factor": 0.46, "ramp_score": 0.45, "voltage_bias": 1.00},
    "hydropower": {"capacity_factor": 0.58, "ramp_score": 0.95, "voltage_bias": 1.20},
    "bioenergy": {"capacity_factor": 0.72, "ramp_score": 0.75, "voltage_bias": 0.95},
    "oil_gas": {"capacity_factor": 0.50, "ramp_score": 0.90, "voltage_bias": 1.10},
    "coal": {"capacity_factor": 0.62, "ramp_score": 0.70, "voltage_bias": 1.05},
    "nuclear": {"capacity_factor": 0.90, "ramp_score": 0.40, "voltage_bias": 1.25},
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


@dataclass(frozen=True)
class SourceSpec:
    file_name: str
    technology: str
    country_columns: tuple[str, ...]
    name_column: str
    unit_column: str | None
    capacity_column: str
    status_column: str
    state_column: str | None
    latitude_column: str
    longitude_column: str


SOURCES = [
    SourceSpec(
        "solar.csv",
        "solar",
        ("country/area",),
        "project_name",
        "phase_name",
        "capacity_(mw)",
        "status",
        "state/province",
        "latitude",
        "longitude",
    ),
    SourceSpec(
        "wind.csv",
        "wind",
        ("country/area",),
        "project_name",
        "phase_name",
        "capacity_(mw)",
        "status",
        "state/province",
        "latitude",
        "longitude",
    ),
    SourceSpec(
        "hydropower.csv",
        "hydropower",
        ("country/area_1", "country/area_2"),
        "project_name",
        None,
        "capacity_(mw)",
        "status",
        "state/province_1",
        "latitude",
        "longitude",
    ),
    SourceSpec(
        "bioenergy.csv",
        "bioenergy",
        ("country/area",),
        "project_name",
        "unit_name",
        "capacity_(mw)",
        "status",
        "state/province",
        "latitude",
        "longitude",
    ),
    SourceSpec(
        "oil_and_gas_plants.csv",
        "oil_gas",
        ("country/area",),
        "plant_name",
        "unit_name",
        "capacity_(mw)",
        "status",
        "state/province",
        "latitude",
        "longitude",
    ),
    SourceSpec(
        "coal_plants.csv",
        "coal",
        ("country/area",),
        "plant_name",
        "unit_name",
        "capacity_(mw)",
        "status",
        "subnational_unit_(province,_state)",
        "latitude",
        "longitude",
    ),
    SourceSpec(
        "nuclear.csv",
        "nuclear",
        ("country/area",),
        "project_name",
        "unit_name",
        "capacity_(mw)",
        "status",
        "state/province",
        "latitude",
        "longitude",
    ),
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


def has_brazil(row: pd.Series, columns: tuple[str, ...]) -> bool:
    return any("brazil" in str(row.get(column, "")).lower() for column in columns)


def normalize_state(value: object) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return STATE_ALIASES.get(text)


def load_generators() -> gpd.GeoDataFrame:
    frames: list[pd.DataFrame] = []

    for spec in SOURCES:
        path = ENERGY_DIR / spec.file_name
        df = pd.read_csv(path, low_memory=False)
        df = df[df.apply(lambda row: has_brazil(row, spec.country_columns), axis=1)].copy()
        df["status_norm"] = df[spec.status_column].astype(str).str.lower().str.strip()
        df = df[df["status_norm"].isin(ACTIVE_STATUSES)].copy()

        df["capacity_mw"] = clean_number(df[spec.capacity_column])
        df["latitude"] = clean_number(df[spec.latitude_column])
        df["longitude"] = clean_number(df[spec.longitude_column])
        df = df.dropna(subset=["capacity_mw", "latitude", "longitude"])
        df = df[df["capacity_mw"] > 0].copy()
        df = df[df["latitude"].between(-35, 6) & df["longitude"].between(-75, -25)].copy()

        name = df[spec.name_column].fillna("Unnamed asset").astype(str)
        if spec.unit_column and spec.unit_column in df.columns:
            unit = df[spec.unit_column].fillna("").astype(str)
            df["asset_name"] = np.where(unit.str.strip().isin(["", "--", "nan"]), name, name + " - " + unit)
        else:
            df["asset_name"] = name

        assumptions = TECH_ASSUMPTIONS[spec.technology]
        df["technology"] = spec.technology
        df["source_file"] = spec.file_name
        df["state_code"] = df[spec.state_column].map(normalize_state) if spec.state_column else None
        df["dispatchable_mw"] = df["capacity_mw"] * assumptions["capacity_factor"]
        df["ramp_score"] = assumptions["ramp_score"]
        df["generator_id"] = [
            f"{spec.technology}_{i:05d}" for i in range(len(df))
        ]
        keep = [
            "generator_id",
            "asset_name",
            "technology",
            "status_norm",
            "capacity_mw",
            "dispatchable_mw",
            "ramp_score",
            "state_code",
            "latitude",
            "longitude",
            "source_file",
        ]
        frames.append(df[keep])

    generators = pd.concat(frames, ignore_index=True)
    generators["generator_id"] = [f"gen_{i:05d}" for i in range(len(generators))]
    geometry = gpd.points_from_xy(generators["longitude"], generators["latitude"])
    return gpd.GeoDataFrame(generators, geometry=geometry, crs="EPSG:4326")


def load_fuel_infrastructure() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    point_specs = [
        ("lng_terminals.csv", "lng_terminal", ("country/area",), "terminalname", "status", "latitude", "longitude"),
        ("coal_mines.csv", "coal_mine", ("country_/_area",), "mine_name", "status", "latitude", "longitude"),
        ("coal_terminals.csv", "coal_terminal", ("country/area",), "coal_terminal_name", "status", "latitude", "longitude"),
        ("extraction_field_main.csv", "oil_gas_field", ("country/area",), "unit_name", "status", "latitude", "longitude"),
    ]
    corridor_specs = [
        ("gas_pipelines.csv", "gas_pipeline", ("countriesorareas", "startcountryorarea", "endcountryorarea"), "pipelinename", "status"),
        ("oil_pipelines.csv", "oil_pipeline", ("countries", "startcountry", "endcountry"), "pipelinename", "status"),
    ]

    def parse_wkt(value: object):
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return wkt.loads(value)
        except Exception:
            return None

    point_frames = []
    for file_name, asset_type, country_columns, name_column, status_column, lat_column, lon_column in point_specs:
        df = pd.read_csv(ENERGY_DIR / file_name, low_memory=False)
        df = df[df.apply(lambda row: has_brazil(row, country_columns), axis=1)].copy()
        df["latitude"] = clean_number(df[lat_column])
        df["longitude"] = clean_number(df[lon_column])
        df = df.dropna(subset=["latitude", "longitude"])
        df = df[df["latitude"].between(-35, 6) & df["longitude"].between(-75, -25)].copy()
        if df.empty:
            continue
        df["asset_type"] = asset_type
        df["asset_name"] = df[name_column].fillna("Unnamed infrastructure").astype(str)
        df["status_norm"] = df[status_column].fillna("unknown").astype(str).str.lower().str.strip()
        df["source_file"] = file_name
        point_frames.append(df[["asset_type", "asset_name", "status_norm", "latitude", "longitude", "source_file"]])

    if point_frames:
        fuel_points = pd.concat(point_frames, ignore_index=True)
        fuel_points = gpd.GeoDataFrame(
            fuel_points,
            geometry=gpd.points_from_xy(fuel_points["longitude"], fuel_points["latitude"]),
            crs="EPSG:4326",
        )
    else:
        fuel_points = gpd.GeoDataFrame(
            columns=["asset_type", "asset_name", "status_norm", "latitude", "longitude", "source_file", "geometry"],
            geometry="geometry",
            crs="EPSG:4326",
        )

    corridor_frames = []
    for file_name, asset_type, country_columns, name_column, status_column in corridor_specs:
        df = pd.read_csv(ENERGY_DIR / file_name, low_memory=False)
        df = df[df.apply(lambda row: has_brazil(row, country_columns), axis=1)].copy()
        df = df.dropna(subset=["geometry"]).copy()
        if df.empty:
            continue
        df["geometry"] = df["geometry"].map(parse_wkt)
        df = df.dropna(subset=["geometry"]).copy()
        corridors = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")
        corridors = corridors[corridors.intersects(Point(-51.9, -14.2).buffer(25))].copy()
        corridors["asset_type"] = asset_type
        corridors["asset_name"] = corridors[name_column].fillna("Unnamed corridor").astype(str)
        corridors["status_norm"] = corridors[status_column].fillna("unknown").astype(str).str.lower().str.strip()
        corridors["source_file"] = file_name
        corridors["length_km_reported"] = clean_number(corridors.get("lengthmergedkm", pd.Series(index=corridors.index)))
        corridor_frames.append(corridors[["asset_type", "asset_name", "status_norm", "length_km_reported", "source_file", "geometry"]])

    if corridor_frames:
        fuel_corridors = gpd.GeoDataFrame(pd.concat(corridor_frames, ignore_index=True), crs="EPSG:4326")
    else:
        fuel_corridors = gpd.GeoDataFrame(
            columns=["asset_type", "asset_name", "status_norm", "length_km_reported", "source_file", "geometry"],
            geometry="geometry",
            crs="EPSG:4326",
        )

    return fuel_points, fuel_corridors


def weighted_bus_count(generators: gpd.GeoDataFrame) -> int:
    n = len(generators)
    total_capacity = generators["capacity_mw"].sum()
    return int(np.clip(math.sqrt(n) * 2.3 + total_capacity / 4500, 180, 360))


def make_buses(generators: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    from sklearn.cluster import MiniBatchKMeans

    n_buses = min(weighted_bus_count(generators), len(generators))
    coords = generators[["longitude", "latitude"]].to_numpy()
    weights = np.sqrt(generators["capacity_mw"].clip(lower=1).to_numpy())

    model = MiniBatchKMeans(
        n_clusters=n_buses,
        random_state=42,
        batch_size=2048,
        n_init=10,
        reassignment_ratio=0.01,
    )
    labels = model.fit_predict(coords, sample_weight=weights)
    generators = generators.copy()
    generators["bus_id"] = [f"bus_{label:03d}" for label in labels]

    grouped = generators.groupby("bus_id", as_index=False)
    buses = grouped.agg(
        latitude=("latitude", lambda s: np.average(s, weights=generators.loc[s.index, "capacity_mw"])),
        longitude=("longitude", lambda s: np.average(s, weights=generators.loc[s.index, "capacity_mw"])),
        capacity_mw=("capacity_mw", "sum"),
        available_generation_mw=("dispatchable_mw", "sum"),
        generator_count=("generator_id", "count"),
        ramp_score=("ramp_score", "mean"),
    )

    tech_mix = (
        generators.pivot_table(index="bus_id", columns="technology", values="capacity_mw", aggfunc="sum", fill_value=0)
        .reset_index()
    )
    buses = buses.merge(tech_mix, on="bus_id", how="left")
    for technology in TECH_ASSUMPTIONS:
        if technology not in buses.columns:
            buses[technology] = 0.0

    conventional = buses[["hydropower", "bioenergy", "oil_gas", "coal", "nuclear"]].sum(axis=1)
    cap_weight = buses["capacity_mw"] / buses["capacity_mw"].sum()
    count_weight = np.sqrt(buses["generator_count"]) / np.sqrt(buses["generator_count"]).sum()
    conv_weight = conventional / conventional.sum() if conventional.sum() else cap_weight
    load_weight = (0.55 * cap_weight + 0.25 * count_weight + 0.20 * conv_weight)
    load_weight = load_weight / load_weight.sum()

    total_generation = buses["available_generation_mw"].sum()
    buses["load_mw"] = total_generation * load_weight
    buses["net_injection_mw"] = buses["available_generation_mw"] - buses["load_mw"]
    buses["dominant_technology"] = buses[list(TECH_ASSUMPTIONS)].idxmax(axis=1)
    buses["voltage_kv"] = np.select(
        [buses["capacity_mw"] >= 1800, buses["capacity_mw"] >= 650],
        [500, 230],
        default=138,
    )
    buses = gpd.GeoDataFrame(
        buses,
        geometry=gpd.points_from_xy(buses["longitude"], buses["latitude"]),
        crs="EPSG:4326",
    )
    return generators, buses


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def choose_voltage(length_km: float, cap_a: float, cap_b: float) -> int:
    endpoint_scale = max(cap_a, cap_b)
    if length_km >= 260 or endpoint_scale >= 2200:
        return 500
    if length_km >= 85 or endpoint_scale >= 650:
        return 230
    return 138


def make_branches(buses: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    coords = buses[["longitude", "latitude"]].to_numpy()
    edge_pairs: set[tuple[int, int]] = set()

    tri = Delaunay(coords)
    for simplex in tri.simplices:
        for a, b in ((simplex[0], simplex[1]), (simplex[1], simplex[2]), (simplex[2], simplex[0])):
            edge_pairs.add(tuple(sorted((int(a), int(b)))))

    tree = cKDTree(coords)
    _, idx = tree.query(coords, k=min(6, len(buses)))
    for i, neighbors in enumerate(idx):
        for j in neighbors[1:]:
            edge_pairs.add(tuple(sorted((int(i), int(j)))))

    records = []
    bus_rows = buses.reset_index(drop=True)
    for a, b in sorted(edge_pairs):
        row_a = bus_rows.iloc[a]
        row_b = bus_rows.iloc[b]
        length_km = haversine_km(row_a.longitude, row_a.latitude, row_b.longitude, row_b.latitude)
        if length_km > 850:
            continue

        voltage_kv = choose_voltage(length_km, row_a.capacity_mw, row_b.capacity_mw)
        z_base = voltage_kv**2 / 100.0
        x_ohm_per_km = 0.36 if voltage_kv >= 500 else 0.42 if voltage_kv >= 230 else 0.48
        x_pu = max((x_ohm_per_km * length_km) / z_base, 0.0005)
        capacity_boost = 1.0 + min(row_a.capacity_mw + row_b.capacity_mw, 4500) / 9000
        thermal_limit_mw = {138: 260, 230: 760, 500: 2300}[voltage_kv] * capacity_boost
        records.append(
            {
                "branch_id": f"branch_{len(records):04d}",
                "from_bus": row_a.bus_id,
                "to_bus": row_b.bus_id,
                "length_km": length_km,
                "voltage_kv": voltage_kv,
                "x_pu": x_pu,
                "thermal_limit_mw": thermal_limit_mw,
                "geometry": LineString([row_a.geometry, row_b.geometry]),
            }
        )

    branches = gpd.GeoDataFrame(records, crs="EPSG:4326")
    graph = nx.Graph()
    graph.add_nodes_from(buses["bus_id"])
    graph.add_edges_from(branches[["from_bus", "to_bus"]].itertuples(index=False, name=None))
    if not nx.is_connected(graph):
        components = [list(c) for c in nx.connected_components(graph)]
        centroids = buses.set_index("bus_id")
        while len(components) > 1:
            base = components.pop(0)
            best = None
            for other_index, other in enumerate(components):
                for a in base:
                    for b in other:
                        ra = centroids.loc[a]
                        rb = centroids.loc[b]
                        length_km = haversine_km(ra.longitude, ra.latitude, rb.longitude, rb.latitude)
                        if best is None or length_km < best[0]:
                            best = (length_km, a, b, other_index)
            assert best is not None
            length_km, a, b, other_index = best
            ra = centroids.loc[a]
            rb = centroids.loc[b]
            voltage_kv = choose_voltage(length_km, ra.capacity_mw, rb.capacity_mw)
            z_base = voltage_kv**2 / 100.0
            x_pu = max((0.36 * length_km) / z_base, 0.0005)
            new_row = {
                "branch_id": f"branch_{len(branches):04d}",
                "from_bus": a,
                "to_bus": b,
                "length_km": length_km,
                "voltage_kv": voltage_kv,
                "x_pu": x_pu,
                "thermal_limit_mw": {138: 260, 230: 760, 500: 2300}[voltage_kv],
                "geometry": LineString([ra.geometry, rb.geometry]),
            }
            branches = pd.concat([branches, gpd.GeoDataFrame([new_row], crs="EPSG:4326")], ignore_index=True)
            base.extend(components.pop(other_index))
            components.insert(0, base)

    return branches


def solve_dc_power_flow(buses: gpd.GeoDataFrame, branches: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict]:
    buses = buses.copy().reset_index(drop=True)
    branches = branches.copy().reset_index(drop=True)
    bus_index = {bus_id: i for i, bus_id in enumerate(buses["bus_id"])}
    n = len(buses)
    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []

    for branch in branches.itertuples(index=False):
        i = bus_index[branch.from_bus]
        j = bus_index[branch.to_bus]
        b = 1.0 / branch.x_pu
        rows.extend([i, j, i, j])
        cols.extend([i, j, j, i])
        data.extend([b, b, -b, -b])

    b_matrix = csr_matrix((data, (rows, cols)), shape=(n, n))
    p = buses["net_injection_mw"].to_numpy() / 100.0
    p = p - p.mean()
    slack = int(np.argmax(buses["available_generation_mw"].to_numpy()))
    keep = np.array([i for i in range(n) if i != slack])
    theta = np.zeros(n)
    theta[keep] = spsolve(b_matrix[keep][:, keep], p[keep])

    flows = []
    for branch in branches.itertuples(index=False):
        i = bus_index[branch.from_bus]
        j = bus_index[branch.to_bus]
        flow_mw = (theta[i] - theta[j]) / branch.x_pu * 100.0
        flows.append(flow_mw)
    branches["flow_mw"] = flows
    branches["abs_flow_mw"] = branches["flow_mw"].abs()
    branches["utilization"] = branches["abs_flow_mw"] / branches["thermal_limit_mw"]
    branches["overloaded"] = branches["utilization"] > 1.0
    buses["voltage_angle_rad"] = theta
    buses["voltage_angle_deg"] = np.degrees(theta)

    summary = {
        "bus_count": int(len(buses)),
        "branch_count": int(len(branches)),
        "generator_count": int(buses["generator_count"].sum()),
        "installed_capacity_mw": float(buses["capacity_mw"].sum()),
        "available_generation_mw": float(buses["available_generation_mw"].sum()),
        "synthetic_load_mw": float(buses["load_mw"].sum()),
        "max_branch_utilization": float(branches["utilization"].max()),
        "overloaded_branch_count": int(branches["overloaded"].sum()),
        "slack_bus": str(buses.loc[slack, "bus_id"]),
        "model_notes": [
            "Transmission corridors are inferred from plant geography with Delaunay and nearest-neighbor connectivity because no transmission-line dataset was present in clean_data/energy.",
            "Power flow uses a lossless DC approximation with branch reactance derived from inferred voltage and great-circle distance.",
            "Loads are synthetic and allocated from generation geography, generator counts, and dispatchable capacity because no measured demand dataset was present.",
        ],
    }
    return buses, branches, summary


def write_outputs(
    generators: gpd.GeoDataFrame,
    buses: gpd.GeoDataFrame,
    branches: gpd.GeoDataFrame,
    fuel_points: gpd.GeoDataFrame,
    fuel_corridors: gpd.GeoDataFrame,
    summary: dict,
) -> None:
    generators.drop(columns="geometry").to_csv(OUT_DIR / "brazil_power_grid_generators.csv", index=False)
    buses.drop(columns="geometry").to_csv(OUT_DIR / "brazil_power_grid_buses.csv", index=False)
    branches.drop(columns="geometry").to_csv(OUT_DIR / "brazil_power_grid_branches.csv", index=False)

    gpkg = OUT_DIR / "brazil_power_grid_model.gpkg"
    if gpkg.exists():
        gpkg.unlink()
    generators.to_file(gpkg, layer="generators", driver="GPKG")
    buses.to_file(gpkg, layer="buses", driver="GPKG")
    branches.to_file(gpkg, layer="branches", driver="GPKG")
    fuel_points.to_file(gpkg, layer="fuel_points", driver="GPKG")
    fuel_corridors.to_file(gpkg, layer="fuel_corridors", driver="GPKG")
    (OUT_DIR / "brazil_power_grid_summary.json").write_text(json.dumps(summary, indent=2))


def utilization_color(utilization: float) -> str:
    if utilization >= 1.0:
        return "#dc2626"
    if utilization >= 0.75:
        return "#f97316"
    if utilization >= 0.50:
        return "#eab308"
    return "#2563eb"


def make_map(
    generators: gpd.GeoDataFrame,
    buses: gpd.GeoDataFrame,
    branches: gpd.GeoDataFrame,
    fuel_points: gpd.GeoDataFrame,
    fuel_corridors: gpd.GeoDataFrame,
    summary: dict,
) -> None:
    m = folium.Map(location=[-14.2, -51.9], zoom_start=4, tiles="CartoDB positron", control_scale=True)
    folium.TileLayer("CartoDB dark_matter", name="Dark matter").add_to(m)

    branch_group = folium.FeatureGroup(name="Inferred transmission branches", show=True)
    for row in branches.itertuples():
        coords = [(lat, lon) for lon, lat in row.geometry.coords]
        folium.PolyLine(
            coords,
            color=utilization_color(row.utilization),
            weight=max(1.0, min(7.0, row.voltage_kv / 95)),
            opacity=0.78,
            tooltip=(
                f"{row.from_bus} → {row.to_bus}<br>"
                f"{row.voltage_kv:.0f} kV inferred<br>"
                f"{row.length_km:.1f} km<br>"
                f"Flow: {row.flow_mw:.1f} MW<br>"
                f"Utilization: {row.utilization:.0%}"
            ),
        ).add_to(branch_group)
    branch_group.add_to(m)

    corridor_group = folium.FeatureGroup(name="Fuel corridors", show=False)
    corridor_colors = {"gas_pipeline": "#ef4444", "oil_pipeline": "#111827"}
    for row in fuel_corridors.itertuples():
        geometries = list(row.geometry.geoms) if row.geometry.geom_type == "MultiLineString" else [row.geometry]
        for geom in geometries:
            coords = [(lat, lon) for lon, lat in geom.coords]
            folium.PolyLine(
                coords,
                color=corridor_colors.get(row.asset_type, "#334155"),
                weight=2.2,
                opacity=0.62,
                dash_array="6 5",
                tooltip=(
                    f"{row.asset_name}<br>{row.asset_type.replace('_', ' ')}<br>"
                    f"{row.status_norm}<br>{row.length_km_reported if pd.notna(row.length_km_reported) else 'unknown'} km reported"
                ),
            ).add_to(corridor_group)
    corridor_group.add_to(m)

    bus_group = folium.FeatureGroup(name="Electrical buses", show=True)
    for row in buses.itertuples():
        radius = 4 + 11 * math.sqrt(max(row.capacity_mw, 0) / max(buses["capacity_mw"].max(), 1))
        fill = "#16a34a" if row.net_injection_mw >= 0 else "#7c3aed"
        folium.CircleMarker(
            location=[row.latitude, row.longitude],
            radius=radius,
            color="#111827",
            weight=0.8,
            fill=True,
            fill_color=fill,
            fill_opacity=0.72,
            tooltip=(
                f"{row.bus_id}<br>"
                f"Dominant: {row.dominant_technology}<br>"
                f"Capacity: {row.capacity_mw:,.0f} MW<br>"
                f"Generation: {row.available_generation_mw:,.0f} MW<br>"
                f"Load: {row.load_mw:,.0f} MW<br>"
                f"Net injection: {row.net_injection_mw:,.0f} MW<br>"
                f"Angle: {row.voltage_angle_deg:.2f}°"
            ),
        ).add_to(bus_group)
    bus_group.add_to(m)

    heat_data = generators[["latitude", "longitude", "capacity_mw"]].dropna().values.tolist()
    HeatMap(
        heat_data,
        name="Generator capacity heatmap",
        radius=10,
        blur=14,
        min_opacity=0.20,
        max_zoom=7,
        show=False,
    ).add_to(m)

    top_generators = generators.nlargest(350, "capacity_mw")
    top_group = folium.FeatureGroup(name="Top generators by capacity", show=False)
    tech_colors = {
        "solar": "#f59e0b",
        "wind": "#06b6d4",
        "hydropower": "#2563eb",
        "bioenergy": "#16a34a",
        "oil_gas": "#ef4444",
        "coal": "#57534e",
        "nuclear": "#7c3aed",
    }
    for row in top_generators.itertuples():
        folium.CircleMarker(
            location=[row.latitude, row.longitude],
            radius=3 + 7 * math.sqrt(row.capacity_mw / max(top_generators["capacity_mw"].max(), 1)),
            color=tech_colors.get(row.technology, "#111827"),
            fill=True,
            fill_color=tech_colors.get(row.technology, "#111827"),
            fill_opacity=0.82,
            weight=1,
            tooltip=f"{row.asset_name}<br>{row.technology}<br>{row.capacity_mw:,.1f} MW<br>{row.status_norm}",
        ).add_to(top_group)
    top_group.add_to(m)

    fuel_group = folium.FeatureGroup(name="Fuel supply assets", show=False)
    fuel_colors = {
        "lng_terminal": "#ef4444",
        "coal_mine": "#57534e",
        "coal_terminal": "#92400e",
        "oil_gas_field": "#0f766e",
    }
    for row in fuel_points.itertuples():
        folium.CircleMarker(
            location=[row.latitude, row.longitude],
            radius=4,
            color=fuel_colors.get(row.asset_type, "#334155"),
            fill=True,
            fill_color=fuel_colors.get(row.asset_type, "#334155"),
            fill_opacity=0.76,
            weight=1,
            tooltip=f"{row.asset_name}<br>{row.asset_type.replace('_', ' ')}<br>{row.status_norm}",
        ).add_to(fuel_group)
    fuel_group.add_to(m)

    legend = f"""
    <div style="position: fixed; bottom: 24px; left: 24px; z-index: 9999; background: white;
      padding: 14px 16px; border: 1px solid #d1d5db; border-radius: 8px;
      box-shadow: 0 10px 25px rgba(15,23,42,.16); font-family: Inter, Arial, sans-serif;
      color: #111827; max-width: 340px;">
      <div style="font-weight: 800; margin-bottom: 6px;">Brazil Physics-Based Power Grid</div>
      <div style="font-size: 12px; line-height: 1.35; margin-bottom: 8px;">
        {summary['bus_count']:,} buses, {summary['branch_count']:,} inferred branches,
        {summary['installed_capacity_mw']:,.0f} MW installed.
      </div>
      <div><span style="display:inline-block;width:22px;height:4px;background:#2563eb;margin-right:8px;"></span>&lt;50% branch loading</div>
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
    m.save(OUT_DIR / "brazil_power_grid_interactive_map.html")


def main() -> None:
    generators = load_generators()
    fuel_points, fuel_corridors = load_fuel_infrastructure()
    generators, buses = make_buses(generators)
    branches = make_branches(buses)
    buses, branches, summary = solve_dc_power_flow(buses, branches)
    summary["fuel_point_count"] = int(len(fuel_points))
    summary["fuel_corridor_count"] = int(len(fuel_corridors))
    write_outputs(generators, buses, branches, fuel_points, fuel_corridors, summary)
    make_map(generators, buses, branches, fuel_points, fuel_corridors, summary)

    print(json.dumps(summary, indent=2))
    print(f"Outputs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
