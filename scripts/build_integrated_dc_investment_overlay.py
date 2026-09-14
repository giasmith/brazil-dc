from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Iterable

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
from branca.colormap import LinearColormap
from folium.plugins import Fullscreen, HeatMap, MarkerCluster, MeasureControl, MiniMap


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "clean_data" / "integrated_dc_investment"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TERRITORIAL_GPKG = ROOT / "clean_data" / "brazil_territorial_layers.gpkg"
ONS_GRID_GPKG = ROOT / "clean_data" / "ons_official_grid" / "ons_official_grid_model.gpkg"
ONS_BRANCH_SCENARIO_CSV = ROOT / "clean_data" / "gem_proposed_overlay" / "ons_branches_gem_proposed_scenario.csv"
ONS_GENERATION_CSV = ROOT / "clean_data" / "ons_official_grid" / "ons_clean_generation_assignments.csv"
GEM_CURRENT_GPKG = ROOT / "clean_data" / "power_grid" / "brazil_power_grid_model.gpkg"
GEM_PROPOSED_GPKG = ROOT / "clean_data" / "gem_proposed_overlay" / "gem_proposed_overlay_model.gpkg"
IDC_GPKG = ROOT / "clean_data" / "idc_data_centers" / "brazil_idc_points_merged.gpkg"
CURTAILMENT_STATE_CSV = ROOT / "clean_data" / "ons_curtailment_history" / "curtailment_by_state_total.csv"

EQUAL_AREA_CRS = "EPSG:6933"

TECH_COLORS = {
    "hydropower": "#2b8cbe",
    "wind": "#7bccc4",
    "solar": "#fdb863",
    "bioenergy": "#4daf4a",
    "oil_gas": "#6b4c3b",
    "coal": "#252525",
    "nuclear": "#b35806",
    "other_thermal": "#969696",
    "other": "#756bb1",
}

BIOME_CONTEXT = {
    "AC": ("Amazonia", "Amazon forest: high ecological sensitivity, humidity, remoteness; lower preliminary DC fit.", 0.30),
    "AL": ("Atlantic Forest / Caatinga", "Coastal demand access but warmer and partly water-stressed.", 0.50),
    "AP": ("Amazonia", "Amazon forest: high ecological sensitivity and limited grid/interconnection depth.", 0.30),
    "AM": ("Amazonia", "Amazon forest: high ecological sensitivity, humidity, and grid distance.", 0.28),
    "BA": ("Caatinga / Cerrado / Atlantic Forest", "Huge renewable resource, but hotter climate and transmission constraints matter.", 0.55),
    "CE": ("Caatinga / coastal", "Strong subsea and renewable position, but hot climate and Northeast grid stress.", 0.52),
    "DF": ("Cerrado", "Central grid/administrative node; Cerrado water sensitivity is a siting constraint.", 0.68),
    "ES": ("Atlantic Forest / coastal", "Coastal grid access with Atlantic Forest permitting sensitivity.", 0.68),
    "GO": ("Cerrado", "Central grid and land availability; water/cerrado sensitivity is moderate.", 0.68),
    "MA": ("Amazonia / Cerrado / coastal", "Transition biome with renewable potential but weaker IDC ecosystem.", 0.48),
    "MG": ("Cerrado / Atlantic Forest / Caatinga", "Large grid and hydro/solar base; inland heat/water varies by site.", 0.76),
    "MS": ("Cerrado / Pantanal / Atlantic Forest", "Grid and land availability, but Pantanal/Cerrado constraints need screening.", 0.64),
    "MT": ("Cerrado / Amazonia / Pantanal", "Strong land/solar potential, but high biome sensitivity and distance from IDC clusters.", 0.56),
    "PA": ("Amazonia", "Large hydro/renewable base, but Amazon biome and distance/permitting risks are high.", 0.35),
    "PB": ("Caatinga / Atlantic Forest", "Smaller market, hot climate, coastal/interior water constraints.", 0.48),
    "PE": ("Caatinga / Atlantic Forest", "Northeast market access, but hotter climate and grid constraints.", 0.52),
    "PI": ("Caatinga / Cerrado", "Renewable resource but heat/water/grid constraints are high.", 0.42),
    "PR": ("Atlantic Forest / Cerrado", "Cooler South/Southeast position, strong hydro/grid, good preliminary DC fit.", 0.86),
    "RJ": ("Atlantic Forest / coastal", "Strong interconnection/demand and power assets, but dense coastal siting constraints.", 0.74),
    "RN": ("Caatinga / coastal", "Excellent wind resource but high curtailment/grid stress and hot climate.", 0.48),
    "RO": ("Amazonia", "Amazon biome sensitivity and limited IDC/grid depth.", 0.32),
    "RR": ("Amazonia", "Remote Amazon grid context and high ecological sensitivity.", 0.26),
    "RS": ("Pampa / Atlantic Forest", "Cooler climate, existing IDC market, wind potential; strong preliminary fit.", 0.87),
    "SC": ("Atlantic Forest", "Cooler climate, good South market access; protected Atlantic Forest screening needed.", 0.84),
    "SE": ("Atlantic Forest / Caatinga", "Small market, warm climate, coastal constraints.", 0.50),
    "SP": ("Atlantic Forest / Cerrado", "Largest IDC/interconnection market and grid density; best baseline DC fit.", 0.90),
    "TO": ("Cerrado / Amazonia", "Central renewable potential, but weaker IDC ecosystem and biome sensitivity.", 0.55),
}


def log_norm(series: pd.Series) -> pd.Series:
    values = np.log1p(pd.to_numeric(series, errors="coerce").fillna(0).clip(lower=0))
    if values.max() == values.min():
        return pd.Series(0.0, index=series.index)
    return (values - values.min()) / (values.max() - values.min())


def minmax(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").fillna(0)
    if values.max() == values.min():
        return pd.Series(0.0, index=series.index)
    return (values - values.min()) / (values.max() - values.min())


def sanitize_geometry(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    try:
        gdf["geometry"] = gdf.geometry.make_valid()
    except Exception:
        gdf["geometry"] = gdf.buffer(0)
    return gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()


def read_layers() -> dict[str, gpd.GeoDataFrame]:
    territorial = gpd.read_file(TERRITORIAL_GPKG, layer="all_layers").to_crs("EPSG:4326")
    territorial = sanitize_geometry(territorial)
    states = territorial[territorial["source_layer"].eq("states")].copy()
    protected = territorial[territorial["source_layer"].eq("protected_lands")].copy()
    indigenous = territorial[territorial["source_layer"].eq("indigenous_lands")].copy()

    ons_buses = gpd.read_file(ONS_GRID_GPKG, layer="buses").to_crs("EPSG:4326")
    ons_branches = gpd.read_file(ONS_GRID_GPKG, layer="branches").to_crs("EPSG:4326")
    scenario = pd.read_csv(ONS_BRANCH_SCENARIO_CSV)
    ons_branches = ons_branches.merge(
        scenario[
            [
                "branch_id",
                "scenario_net_injection_mw_utilization",
                "scenario_net_injection_mw_overload_mw",
                "scenario_net_injection_mw_overloaded",
                "delta_abs_flow_mw",
                "delta_utilization",
            ]
        ],
        on="branch_id",
        how="left",
    )

    gem_current = gpd.read_file(GEM_CURRENT_GPKG, layer="generators").to_crs("EPSG:4326")
    gem_proposed = gpd.read_file(GEM_PROPOSED_GPKG, layer="proposed_generators").to_crs("EPSG:4326")
    idc = gpd.read_file(IDC_GPKG, layer="idc_points").to_crs("EPSG:4326")

    return {
        "states": states,
        "protected": protected,
        "indigenous": indigenous,
        "ons_buses": ons_buses,
        "ons_branches": ons_branches,
        "gem_current": gem_current,
        "gem_proposed": gem_proposed,
        "idc": idc,
    }


def union_area_by_state(states: gpd.GeoDataFrame, layer: gpd.GeoDataFrame, out_col: str) -> pd.DataFrame:
    states_eq = states[["abbrev_state", "geometry"]].to_crs(EQUAL_AREA_CRS).copy()
    states_eq["state_area_km2"] = states_eq.geometry.area / 1_000_000
    result = states_eq[["abbrev_state", "state_area_km2"]].copy()
    result[out_col] = 0.0

    if layer.empty:
        return result[["abbrev_state", out_col]]

    geom = sanitize_geometry(layer[["geometry"]].copy()).to_crs(EQUAL_AREA_CRS).geometry
    try:
        union_geom = geom.union_all()
    except AttributeError:
        union_geom = geom.unary_union

    areas = []
    for state in states_eq.itertuples():
        try:
            areas.append(state.geometry.intersection(union_geom).area / 1_000_000)
        except Exception:
            areas.append(0.0)
    result[out_col] = areas
    return result[["abbrev_state", out_col]]


def state_base(states: gpd.GeoDataFrame, protected: gpd.GeoDataFrame, indigenous: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    states = states[["abbrev_state", "name_state", "name_region", "geometry"]].copy()
    states_eq = states.to_crs(EQUAL_AREA_CRS)
    states["state_area_km2"] = states_eq.geometry.area / 1_000_000

    protected_area = union_area_by_state(states, protected, "protected_area_km2")
    indigenous_area = union_area_by_state(states, indigenous, "indigenous_area_km2")
    states = states.merge(protected_area, on="abbrev_state", how="left").merge(indigenous_area, on="abbrev_state", how="left")
    states[["protected_area_km2", "indigenous_area_km2"]] = states[["protected_area_km2", "indigenous_area_km2"]].fillna(0)
    states["protected_share_pct"] = states["protected_area_km2"] / states["state_area_km2"] * 100
    states["indigenous_share_pct"] = states["indigenous_area_km2"] / states["state_area_km2"] * 100
    states["land_constraint_share_pct"] = (states["protected_area_km2"] + states["indigenous_area_km2"]) / states["state_area_km2"] * 100

    states["dominant_biome"] = states["abbrev_state"].map(lambda x: BIOME_CONTEXT.get(x, ("Unknown", "", 0.5))[0])
    states["biome_note"] = states["abbrev_state"].map(lambda x: BIOME_CONTEXT.get(x, ("Unknown", "", 0.5))[1])
    states["biome_fit_score"] = states["abbrev_state"].map(lambda x: BIOME_CONTEXT.get(x, ("Unknown", "", 0.5))[2])
    return states


def branch_state_metrics(ons_branches: gpd.GeoDataFrame) -> pd.DataFrame:
    rows = []
    for row in ons_branches.itertuples():
        states = [row.from_state]
        if row.to_state != row.from_state:
            states.append(row.to_state)
        weight = 1.0 / len(states)
        for state in states:
            rows.append(
                {
                    "abbrev_state": state,
                    "branch_weight": weight,
                    "line_km_weighted": row.length_km * weight if pd.notna(row.length_km) else 0,
                    "hv_line_km_weighted": row.length_km * weight if pd.notna(row.length_km) and row.voltage_kv >= 230 else 0,
                    "ehv_line_km_weighted": row.length_km * weight if pd.notna(row.length_km) and row.voltage_kv >= 500 else 0,
                    "base_overloaded_branch_weight": weight if bool(row.overloaded) else 0,
                    "scenario_overloaded_branch_weight": weight if bool(row.scenario_net_injection_mw_overloaded) else 0,
                    "scenario_over_75_branch_weight": weight if row.scenario_net_injection_mw_utilization >= 0.75 else 0,
                    "scenario_overload_mw_weighted": max(float(row.scenario_net_injection_mw_overload_mw or 0), 0) * weight,
                    "scenario_utilization": row.scenario_net_injection_mw_utilization,
                    "base_utilization": row.utilization,
                }
            )
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["abbrev_state"])
    return (
        df.groupby("abbrev_state", as_index=False)
        .agg(
            ons_branch_count_weighted=("branch_weight", "sum"),
            ons_line_km=("line_km_weighted", "sum"),
            ons_hv_line_km=("hv_line_km_weighted", "sum"),
            ons_ehv_line_km=("ehv_line_km_weighted", "sum"),
            base_overloaded_branch_count=("base_overloaded_branch_weight", "sum"),
            scenario_overloaded_branch_count=("scenario_overloaded_branch_weight", "sum"),
            scenario_over_75_branch_count=("scenario_over_75_branch_weight", "sum"),
            scenario_overload_mw=("scenario_overload_mw_weighted", "sum"),
            max_scenario_utilization=("scenario_utilization", "max"),
            mean_scenario_utilization=("scenario_utilization", "mean"),
            max_base_utilization=("base_utilization", "max"),
        )
    )


def aggregate_metrics(layers: dict[str, gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    states = state_base(layers["states"], layers["protected"], layers["indigenous"])

    idc = layers["idc"].copy()
    idc["abbrev_state"] = idc["state_abbrev_spatial"].fillna(idc["state"]).fillna(idc["state_from_source"])
    for col in ["net_count", "ix_count", "carrier_count"]:
        if col in idc.columns:
            idc[col] = pd.to_numeric(idc[col], errors="coerce").fillna(0)
    idc_metrics = (
        idc.groupby("abbrev_state", as_index=False)
        .agg(
            idc_point_count=("data_center_id", "count"),
            peeringdb_point_count=("source", lambda s: int((s == "PeeringDB").sum())),
            osm_point_count=("source", lambda s: int((s == "OpenStreetMap").sum())),
            peeringdb_networks=("net_count", "sum"),
            peeringdb_ix_count=("ix_count", "sum"),
        )
    )

    buses = layers["ons_buses"].copy()
    renewable_cols = ["hydropower", "wind", "solar", "bioenergy"]
    generation_cols = renewable_cols + ["oil_gas", "coal", "nuclear", "other_thermal", "other"]
    for col in generation_cols:
        if col not in buses.columns:
            buses[col] = 0.0
    buses["renewable_installed_mw"] = buses[renewable_cols].sum(axis=1)
    bus_metrics = (
        buses.groupby("id_estado", as_index=False)
        .agg(
            ons_bus_count=("bus_id", "count"),
            ons_hv_bus_count=("voltage_kv", lambda s: int((s >= 230).sum())),
            ons_ehv_bus_count=("voltage_kv", lambda s: int((s >= 500).sum())),
            ons_installed_mw=("installed_mw", "sum"),
            ons_generation_mw=("generation_mw", "sum"),
            ons_load_mw=("load_mw", "sum"),
            ons_renewable_installed_mw=("renewable_installed_mw", "sum"),
        )
        .rename(columns={"id_estado": "abbrev_state"})
    )
    bus_metrics["ons_renewable_share_pct"] = bus_metrics["ons_renewable_installed_mw"] / bus_metrics["ons_installed_mw"].replace(0, np.nan) * 100

    gen = pd.read_csv(ONS_GENERATION_CSV)
    gen_metrics = (
        gen.groupby(["id_estado", "technology"], as_index=False)["installed_mw"].sum()
        .pivot(index="id_estado", columns="technology", values="installed_mw")
        .fillna(0)
        .reset_index()
        .rename(columns={"id_estado": "abbrev_state"})
    )
    for tech in generation_cols:
        if tech not in gen_metrics.columns:
            gen_metrics[tech] = 0
        gen_metrics = gen_metrics.rename(columns={tech: f"ons_{tech}_mw"})

    branch_metrics = branch_state_metrics(layers["ons_branches"])

    current = layers["gem_current"].copy()
    current["is_renewable"] = current["technology"].isin(renewable_cols)
    gem_current_metrics = (
        current.groupby("state_code", as_index=False)
        .agg(
            gem_current_asset_count=("generator_id", "count"),
            gem_current_capacity_mw=("capacity_mw", "sum"),
            gem_current_renewable_capacity_mw=("capacity_mw", lambda s: s[current.loc[s.index, "is_renewable"]].sum()),
        )
        .rename(columns={"state_code": "abbrev_state"})
    )

    proposed = layers["gem_proposed"].copy()
    proposed["is_renewable"] = proposed["technology"].isin(renewable_cols)
    proposed_metrics = (
        proposed.groupby("assigned_state", as_index=False)
        .agg(
            gem_proposed_asset_count=("proposed_id", "count"),
            gem_proposed_capacity_mw=("capacity_mw", "sum"),
            gem_proposed_dispatch_mw=("proposed_dispatch_mw", "sum"),
            gem_proposed_renewable_dispatch_mw=("proposed_dispatch_mw", lambda s: s[proposed.loc[s.index, "is_renewable"]].sum()),
        )
        .rename(columns={"assigned_state": "abbrev_state"})
    )

    curtailment = pd.read_csv(CURTAILMENT_STATE_CSV).rename(columns={"id_estado": "abbrev_state"})
    curtailment = curtailment[["abbrev_state", "curtailed_mwh"]]

    for df in [idc_metrics, bus_metrics, gen_metrics, branch_metrics, gem_current_metrics, proposed_metrics, curtailment]:
        states = states.merge(df, on="abbrev_state", how="left")

    numeric_cols = states.select_dtypes(include=[np.number]).columns
    states[numeric_cols] = states[numeric_cols].fillna(0)
    return add_scores(states)


def add_scores(states: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    states = states.copy()

    states["market_interconnect_score"] = (
        0.50 * log_norm(states["idc_point_count"])
        + 0.35 * log_norm(states["peeringdb_networks"])
        + 0.15 * log_norm(states["peeringdb_ix_count"])
    )
    states["grid_access_score"] = (
        0.25 * log_norm(states["ons_hv_bus_count"])
        + 0.20 * log_norm(states["ons_ehv_bus_count"])
        + 0.25 * log_norm(states["ons_hv_line_km"])
        + 0.30 * log_norm(states["ons_installed_mw"])
    )
    states["clean_power_score"] = (
        0.35 * log_norm(states["ons_renewable_installed_mw"])
        + 0.20 * (states["ons_renewable_share_pct"].clip(0, 100) / 100)
        + 0.25 * log_norm(states["gem_proposed_renewable_dispatch_mw"])
        + 0.10 * log_norm(states["gem_current_renewable_capacity_mw"])
        + 0.10 * log_norm(states["curtailed_mwh"])
    )
    stress_penalty = (
        0.45 * log_norm(states["scenario_overload_mw"])
        + 0.25 * log_norm(states["scenario_overloaded_branch_count"])
        + 0.20 * log_norm(states["scenario_over_75_branch_count"])
        + 0.10 * minmax(states["max_scenario_utilization"])
    )
    states["transmission_headroom_score"] = (1 - stress_penalty).clip(0, 1)

    land_penalty = (states["land_constraint_share_pct"].clip(0, 100) / 100).clip(0, 1)
    states["environment_biome_score"] = (0.55 * (1 - land_penalty) + 0.45 * states["biome_fit_score"]).clip(0, 1)

    states["dc_investor_score"] = 100 * (
        0.30 * states["market_interconnect_score"]
        + 0.20 * states["grid_access_score"]
        + 0.20 * states["clean_power_score"]
        + 0.15 * states["transmission_headroom_score"]
        + 0.15 * states["environment_biome_score"]
    )
    states["dc_investor_rank"] = states["dc_investor_score"].rank(ascending=False, method="min").astype(int)

    def tier(score: float) -> str:
        if score >= 70:
            return "Tier 1 - strongest preliminary fit"
        if score >= 58:
            return "Tier 2 - strong candidate"
        if score >= 48:
            return "Tier 3 - targeted opportunity"
        return "Tier 4 - higher due-diligence burden"

    states["recommendation_tier"] = states["dc_investor_score"].map(tier)
    states["recommendation_note"] = states.apply(recommendation_note, axis=1)
    return states.sort_values("dc_investor_rank")


def recommendation_note(row: pd.Series) -> str:
    positives = []
    cautions = []
    if row.market_interconnect_score >= 0.65:
        positives.append("deep IDC/interconnection ecosystem")
    if row.grid_access_score >= 0.65:
        positives.append("strong ONS grid access")
    if row.clean_power_score >= 0.65:
        positives.append("large clean-power base or pipeline")
    if row.transmission_headroom_score < 0.45:
        cautions.append("modeled transmission stress")
    if row.land_constraint_share_pct >= 25:
        cautions.append("large protected/indigenous land share")
    if row.biome_fit_score < 0.55:
        cautions.append("biome/climate permitting sensitivity")
    if not positives:
        positives.append("selective site-by-site opportunity")
    if not cautions:
        cautions.append("standard permitting and interconnection diligence")
    return f"Pros: {', '.join(positives)}. Watch: {', '.join(cautions)}."


def popup_table(row: pd.Series, fields: Iterable[tuple[str, str]]) -> str:
    parts = []
    for label, key in fields:
        val = row.get(key)
        if val is None or pd.isna(val):
            continue
        if isinstance(val, (float, np.floating)):
            text = f"{val:,.2f}"
        else:
            text = str(val)
        parts.append(f"<tr><th>{html.escape(label)}</th><td>{html.escape(text)}</td></tr>")
    return "<table>" + "".join(parts) + "</table>"


def add_state_layer(m: folium.Map, state_scores: gpd.GeoDataFrame) -> None:
    colormap = LinearColormap(["#f7f7f7", "#c7e9b4", "#41ab5d", "#005a32"], vmin=state_scores["dc_investor_score"].min(), vmax=state_scores["dc_investor_score"].max())
    colormap.caption = "Preliminary data-center investor score"
    colormap.add_to(m)

    web = state_scores.to_crs("EPSG:4326").copy()
    web["geometry"] = web.geometry.simplify(0.02, preserve_topology=True)
    keep_cols = [
        "abbrev_state",
        "name_state",
        "name_region",
        "dc_investor_rank",
        "dc_investor_score",
        "recommendation_tier",
        "idc_point_count",
        "peeringdb_networks",
        "ons_installed_mw",
        "ons_renewable_share_pct",
        "scenario_overload_mw",
        "curtailed_mwh",
        "land_constraint_share_pct",
        "dominant_biome",
        "biome_note",
        "recommendation_note",
        "geometry",
    ]
    folium.GeoJson(
        web[keep_cols],
        name="State suitability score",
        show=True,
        style_function=lambda feature: {
            "fillColor": colormap(feature["properties"]["dc_investor_score"]),
            "color": "#333333",
            "weight": 1.2,
            "fillOpacity": 0.58,
        },
        highlight_function=lambda _: {"weight": 3, "color": "#111111", "fillOpacity": 0.72},
        tooltip=folium.GeoJsonTooltip(
            fields=[
                "dc_investor_rank",
                "abbrev_state",
                "name_state",
                "dc_investor_score",
                "recommendation_tier",
                "idc_point_count",
                "peeringdb_networks",
                "ons_installed_mw",
                "ons_renewable_share_pct",
                "scenario_overload_mw",
                "dominant_biome",
            ],
            aliases=[
                "Rank",
                "UF",
                "State",
                "Score",
                "Tier",
                "IDC points",
                "PeeringDB networks",
                "ONS installed MW",
                "Renewable share %",
                "Scenario overload MW",
                "Biome",
            ],
            localize=True,
            sticky=False,
        ),
        popup=folium.GeoJsonPopup(
            fields=["name_state", "recommendation_note", "biome_note"],
            aliases=["State", "Recommendation", "Biome note"],
            max_width=520,
        ),
    ).add_to(m)


def add_boundary_layers(m: folium.Map, protected: gpd.GeoDataFrame, indigenous: gpd.GeoDataFrame) -> None:
    for name, layer, color, fill in [
        ("Protected lands / conservation units", protected, "#238b45", "#74c476"),
        ("Indigenous lands", indigenous, "#8c510a", "#d8b365"),
    ]:
        web = layer.to_crs("EPSG:4326").copy()
        web["geometry"] = web.geometry.simplify(0.025, preserve_topology=True)
        label_col = "nome_uc" if "Protected" in name else "name_indigenous_land"
        keep = [col for col in [label_col, "categoria", "fase_ti", "abbrev_state", "uf", "geometry"] if col in web.columns]
        folium.GeoJson(
            web[keep],
            name=name,
            show=False,
            style_function=lambda _, color=color, fill=fill: {
                "color": color,
                "weight": 0.6,
                "fillColor": fill,
                "fillOpacity": 0.18,
            },
            tooltip=folium.GeoJsonTooltip(fields=[c for c in keep if c != "geometry"][:3], sticky=False),
        ).add_to(m)


def add_branch_layers(m: folium.Map, branches: gpd.GeoDataFrame) -> None:
    base = folium.FeatureGroup(name="ONS transmission branches - base utilization", show=True)
    stressed = folium.FeatureGroup(name="Proposed generation stress branches >=75%", show=True)

    def base_color(util: float) -> str:
        if util >= 1:
            return "#d73027"
        if util >= 0.75:
            return "#fc8d59"
        if util >= 0.50:
            return "#fee08b"
        return "#4575b4"

    for row in branches.itertuples():
        coords = [(lat, lon) for lon, lat in row.geometry.coords]
        popup = popup_table(
            pd.Series(row._asdict()),
            [
                ("Line", "line_name"),
                ("Voltage kV", "voltage_kv"),
                ("From", "from_substation"),
                ("To", "to_substation"),
                ("Base utilization", "utilization"),
                ("Scenario utilization", "scenario_net_injection_mw_utilization"),
                ("Scenario overload MW", "scenario_net_injection_mw_overload_mw"),
            ],
        )
        folium.PolyLine(
            coords,
            color=base_color(float(row.utilization or 0)),
            weight=1.2 if row.voltage_kv < 500 else 2.4,
            opacity=0.55,
            tooltip=f"{row.line_name} | {row.voltage_kv:g} kV",
            popup=folium.Popup(popup, max_width=520),
        ).add_to(base)

        scen_util = float(row.scenario_net_injection_mw_utilization or 0)
        if scen_util >= 0.75:
            folium.PolyLine(
                coords,
                color="#b2182b" if scen_util >= 1 else "#ef8a62",
                weight=2.2 if scen_util < 1 else 3.4,
                opacity=0.82,
                tooltip=f"Scenario stressed: {row.line_name} | {scen_util:.2f}x",
                popup=folium.Popup(popup, max_width=520),
            ).add_to(stressed)

    base.add_to(m)
    stressed.add_to(m)


def add_point_layers(m: folium.Map, layers: dict[str, gpd.GeoDataFrame]) -> None:
    idc_group = MarkerCluster(name="IDC / data-center points").add_to(m)
    for row in layers["idc"].itertuples():
        popup = popup_table(
            pd.Series(row._asdict()),
            [
                ("Facility", "facility_name"),
                ("Operator", "operator"),
                ("Source", "source"),
                ("City", "city"),
                ("State", "state_abbrev_spatial"),
                ("Networks", "net_count"),
                ("IX count", "ix_count"),
            ],
        )
        folium.CircleMarker(
            location=[row.latitude, row.longitude],
            radius=5,
            color="#54278f",
            fill=True,
            fill_opacity=0.82,
            weight=1,
            tooltip=f"{row.facility_name} | {row.city}",
            popup=folium.Popup(popup, max_width=430),
        ).add_to(idc_group)

    bus_group = MarkerCluster(name="ONS substations / generation buses").add_to(m)
    for row in layers["ons_buses"].itertuples():
        cap = float(row.installed_mw or 0)
        popup = popup_table(
            pd.Series(row._asdict()),
            [
                ("Substation", "nom_subestacao"),
                ("State", "id_estado"),
                ("Voltage kV", "voltage_kv"),
                ("Installed MW", "installed_mw"),
                ("Generation MW", "generation_mw"),
                ("Load MW", "load_mw"),
                ("Dominant tech", "dominant_technology"),
            ],
        )
        color = TECH_COLORS.get(str(row.dominant_technology), "#636363")
        folium.CircleMarker(
            location=[row.latitude, row.longitude],
            radius=max(3, min(12, np.sqrt(cap) / 10)) if cap else 3,
            color=color,
            fill=True,
            fill_opacity=0.72,
            weight=1,
            tooltip=f"{row.nom_subestacao} | {row.id_estado} | {cap:,.0f} MW",
            popup=folium.Popup(popup, max_width=430),
        ).add_to(bus_group)

    current_heat = [
        [row.geometry.y, row.geometry.x, max(float(row.capacity_mw or 0), 1)]
        for row in layers["gem_current"].itertuples()
        if row.geometry is not None
    ]
    proposed_heat = [
        [row.geometry.y, row.geometry.x, max(float(row.proposed_dispatch_mw or 0), 1)]
        for row in layers["gem_proposed"].itertuples()
        if row.geometry is not None
    ]
    HeatMap(current_heat, name="GEM current generator heatmap", min_opacity=0.22, radius=14, blur=18, show=False).add_to(m)
    HeatMap(proposed_heat, name="GEM proposed generator heatmap", min_opacity=0.22, radius=14, blur=18, show=False).add_to(m)

    proposed_group = MarkerCluster(name="GEM proposed generators").add_to(folium.FeatureGroup(name="GEM proposed generator points", show=False).add_to(m))
    for row in layers["gem_proposed"].itertuples():
        color = TECH_COLORS.get(str(row.technology), "#636363")
        folium.CircleMarker(
            location=[row.latitude, row.longitude],
            radius=max(2.5, min(9, np.sqrt(float(row.capacity_mw or 0)) / 18)),
            color=color,
            fill=True,
            fill_opacity=0.70,
            weight=1,
            tooltip=f"{row.asset_name} | {row.technology} | {row.capacity_mw:,.0f} MW",
        ).add_to(proposed_group)


def add_title_and_legend(m: folium.Map, top_state: pd.Series) -> None:
    html_block = f"""
    <div style="position: fixed; top: 16px; left: 50px; z-index: 9999;
                background: rgba(255,255,255,0.96); border: 1px solid #999; border-radius: 6px;
                padding: 10px 12px; font-family: Arial, sans-serif; max-width: 455px;">
      <div style="font-size: 17px; font-weight: 700;">Brazil Integrated Energy, Land, and IDC Overlay</div>
      <div style="font-size: 12px; color: #444; line-height: 1.35;">
        Top preliminary DC state: <b>{html.escape(top_state['abbrev_state'])} - {html.escape(top_state['name_state'])}</b>
        ({top_state['dc_investor_score']:.1f}/100). Toggle layers to inspect ONS topology,
        IDC points, proposed GEM generation, protected lands, and indigenous lands.
      </div>
    </div>
    <div style="position: fixed; bottom: 30px; left: 28px; z-index: 9999;
                background: rgba(255,255,255,0.94); border: 1px solid #999; border-radius: 6px;
                padding: 8px 10px; font-family: Arial, sans-serif; font-size: 12px;">
      <b>Line stress</b><br>
      <span style="color:#4575b4;">&#9473;</span> low base utilization<br>
      <span style="color:#fc8d59;">&#9473;</span> high utilization<br>
      <span style="color:#b2182b;">&#9473;</span> overloaded in proposed scenario<br>
      <span style="color:#54278f;">&#9679;</span> IDC point
    </div>
    """
    m.get_root().html.add_child(folium.Element(html_block))


def write_outputs(state_scores: gpd.GeoDataFrame, layers: dict[str, gpd.GeoDataFrame]) -> dict:
    csv_path = OUT_DIR / "brazil_state_dc_investor_suitability.csv"
    geojson_path = OUT_DIR / "brazil_state_dc_investor_suitability.geojson"
    gpkg_path = OUT_DIR / "brazil_integrated_dc_layers.gpkg"

    for path in [geojson_path, gpkg_path]:
        if path.exists():
            path.unlink()

    state_scores.drop(columns="geometry").to_csv(csv_path, index=False)
    state_scores.to_file(geojson_path, driver="GeoJSON")
    state_scores.to_file(gpkg_path, layer="state_suitability", driver="GPKG")

    layers["idc"].to_file(gpkg_path, layer="idc_points", driver="GPKG")
    layers["ons_buses"].to_file(gpkg_path, layer="ons_buses", driver="GPKG")
    layers["ons_branches"].to_file(gpkg_path, layer="ons_branches_with_scenario_stress", driver="GPKG")
    layers["gem_current"].to_file(gpkg_path, layer="gem_current_generators", driver="GPKG")
    layers["gem_proposed"].to_file(gpkg_path, layer="gem_proposed_generators", driver="GPKG")
    layers["protected"][["feature_id", "nome_uc", "categoria", "grupo", "uf", "geometry"]].to_file(gpkg_path, layer="protected_lands", driver="GPKG")
    layers["indigenous"][["feature_id", "name_indigenous_land", "fase_ti", "modalidade", "abbrev_state", "geometry"]].to_file(gpkg_path, layer="indigenous_lands", driver="GPKG")

    top = state_scores.sort_values("dc_investor_rank").head(8)
    summary = {
        "method": "Preliminary state-level data-center suitability score using local IDC, ONS grid, GEM generation, curtailment, protected/indigenous land, and biome-context layers.",
        "score_weights": {
            "market_interconnect_score": 0.30,
            "grid_access_score": 0.20,
            "clean_power_score": 0.20,
            "transmission_headroom_score": 0.15,
            "environment_biome_score": 0.15,
        },
        "important_caveat": "This is a screening tool, not investment advice. It excludes land price, taxes, utility tariffs, fiber contracts, water rights, permitting timelines, individual parcel constraints, and confidential interconnection queues.",
        "top_states": top[["dc_investor_rank", "abbrev_state", "name_state", "dc_investor_score", "recommendation_tier", "recommendation_note"]].to_dict(orient="records"),
        "outputs": {
            "interactive_map": str(OUT_DIR / "brazil_integrated_dc_infrastructure_map.html"),
            "state_scores_csv": str(csv_path),
            "state_scores_geojson": str(geojson_path),
            "integrated_gpkg": str(gpkg_path),
        },
    }
    summary_path = OUT_DIR / "brazil_integrated_dc_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def make_map(state_scores: gpd.GeoDataFrame, layers: dict[str, gpd.GeoDataFrame]) -> None:
    m = folium.Map(location=[-14.2, -51.9], zoom_start=4, tiles="CartoDB positron", prefer_canvas=True)
    Fullscreen(position="topright").add_to(m)
    MiniMap(toggle_display=True, minimized=True).add_to(m)
    MeasureControl(position="bottomleft").add_to(m)

    add_state_layer(m, state_scores)
    add_boundary_layers(m, layers["protected"], layers["indigenous"])
    add_branch_layers(m, layers["ons_branches"])
    add_point_layers(m, layers)
    add_title_and_legend(m, state_scores.sort_values("dc_investor_rank").iloc[0])
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(OUT_DIR / "brazil_integrated_dc_infrastructure_map.html")


def main() -> None:
    layers = read_layers()
    state_scores = aggregate_metrics(layers)
    summary = write_outputs(state_scores, layers)
    make_map(state_scores, layers)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nTop preliminary states")
    cols = [
        "dc_investor_rank",
        "abbrev_state",
        "name_state",
        "dc_investor_score",
        "recommendation_tier",
        "idc_point_count",
        "peeringdb_networks",
        "ons_installed_mw",
        "ons_renewable_share_pct",
        "scenario_overload_mw",
        "land_constraint_share_pct",
        "dominant_biome",
    ]
    print(state_scores[cols].head(12).to_string(index=False))
    print(f"\nOutputs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
