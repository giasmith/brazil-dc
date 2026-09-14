from __future__ import annotations

import base64
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[1]
ONS_DIR = ROOT / "data" / "ons"
OUT_DIR = ROOT / "clean_data" / "ons_curtailment_detail"
OUT_DIR.mkdir(parents=True, exist_ok=True)

S3_BASE = "https://ons-aws-prod-opendata.s3.amazonaws.com/"
S3_NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}

DATASETS = {
    "wind": {
        "detail_prefix": "dataset/restricao_coff_eolica_detail_tm/",
        "detail_local": ONS_DIR / "restricao_coff_eolica_detail_tm",
        "detail_stem": "RESTRICAO_COFF_EOLICA_DETAIL",
        "agg_local": ONS_DIR / "restricao_coff_eolica_tm",
        "agg_stem": "RESTRICAO_COFF_EOLICA",
        "weather_col": "val_ventoverificado",
        "weather_invalid_col": "flg_dadoventoinvalido",
        "weather_label": "verified_wind_speed",
        "weather_unit": "m/s",
        "weather_bins": [-np.inf, 3, 5, 7, 9, 11, 13, np.inf],
        "weather_bin_labels": ["<3", "3-5", "5-7", "7-9", "9-11", "11-13", "13+"],
        "weather_valid_min": 0,
        "weather_valid_max": 35,
    },
    "solar": {
        "detail_prefix": "dataset/restricao_coff_fotovoltaica_detail_tm/",
        "detail_local": ONS_DIR / "restricao_coff_fotovoltaica_detail_tm",
        "detail_stem": "RESTRICAO_COFF_FOTOVOLTAICA_DETAIL",
        "agg_local": ONS_DIR / "restricao_coff_fotovoltaica_tm",
        "agg_stem": "RESTRICAO_COFF_FOTOVOLTAICA",
        "weather_col": "val_irradianciaverificado",
        "weather_invalid_col": "flg_dadoirradianciainvalido",
        "weather_label": "verified_irradiance",
        "weather_unit": "W/m2",
        "weather_bins": [-np.inf, 50, 200, 400, 600, 800, 1000, np.inf],
        "weather_bin_labels": ["<50", "50-200", "200-400", "400-600", "600-800", "800-1000", "1000+"],
        "weather_valid_min": 0,
        "weather_valid_max": 1400,
    },
}


def strip_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(ch)
    )


def norm_text(value: object) -> str:
    if pd.isna(value):
        return ""
    text = strip_accents(str(value).upper())
    text = re.sub(r"\b(CONJ|CONJUNTO|USINA|PARQUE|COMPLEXO)\b", " ", text)
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def list_s3_keys(prefix: str) -> list[str]:
    keys: list[str] = []
    continuation = None
    while True:
        url = f"{S3_BASE}?list-type=2&prefix={prefix}"
        if continuation:
            url += f"&continuation-token={continuation}"
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        keys.extend(k.find("s:Key", S3_NS).text for k in root.findall("s:Contents", S3_NS))
        token = root.find("s:NextContinuationToken", S3_NS)
        if token is None:
            break
        continuation = token.text
    return keys


def preferred_monthly_files(keys: list[str], stem: str) -> list[str]:
    pattern = re.compile(rf"{re.escape(stem)}_(\d{{4}}_\d{{2}})\.(csv|parquet)$")
    by_month: dict[str, dict[str, str]] = {}
    for key in keys:
        match = pattern.search(key)
        if not match:
            continue
        month, suffix = match.groups()
        by_month.setdefault(month, {})[suffix] = key
    return [by_month[month].get("parquet") or by_month[month]["csv"] for month in sorted(by_month)]


def download_key(key: str, local_dir: Path) -> Path:
    local_dir.mkdir(parents=True, exist_ok=True)
    path = local_dir / Path(key).name
    if path.exists() and path.stat().st_size > 0:
        return path
    response = requests.get(S3_BASE + key, timeout=240)
    response.raise_for_status()
    path.write_bytes(response.content)
    return path


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    with path.open("rb") as handle:
        sample = handle.read(4096).decode("utf-8-sig", errors="replace")
    sep = ";" if sample.count(";") >= sample.count(",") else ","
    return pd.read_csv(path, sep=sep, encoding="utf-8-sig", low_memory=False)


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.fillna("")
        .astype(str)
        .str.strip()
        .str.replace(",", ".", regex=False)
        .replace({"": pd.NA, "-": pd.NA}),
        errors="coerce",
    )


def boolish(series: pd.Series) -> pd.Series:
    text = series.fillna("").astype(str).str.strip().str.upper()
    return text.isin(["1", "1.0", "TRUE", "T", "SIM", "S"])


def month_from_path(path: Path) -> str:
    match = re.search(r"_(\d{4})_(\d{2})\.", path.name)
    if not match:
        raise ValueError(f"Could not parse month from {path.name}")
    return f"{match.group(1)}-{match.group(2)}"


def aggregate_path_for_detail(detail_path: Path, config: dict) -> Path | None:
    month = month_from_path(detail_path).replace("-", "_")
    for suffix in [".parquet", ".csv"]:
        path = config["agg_local"] / f"{config['agg_stem']}_{month}{suffix}"
        if path.exists():
            return path
    return None


def normalize_aggregate(path: Path) -> pd.DataFrame:
    df = read_table(path)
    required = ["id_subsistema", "id_estado", "nom_usina", "din_instante", "cod_razaorestricao", "cod_origemrestricao", "dsc_restricao"]
    for col in ["cod_razaorestricao", "cod_origemrestricao", "dsc_restricao"]:
        if col not in df.columns:
            df[col] = ""
    missing = [col for col in required if col not in df.columns]
    if missing:
        return pd.DataFrame(columns=["id_subsistema", "id_estado", "din_instante", "group_norm", "is_restricted", "is_rel", "is_rel_sis", "is_rel_loc"])
    df = df[required].copy()
    df["din_instante"] = pd.to_datetime(df["din_instante"], errors="coerce")
    df = df.dropna(subset=["din_instante"])
    for col in ["id_subsistema", "id_estado", "nom_usina", "cod_razaorestricao", "cod_origemrestricao", "dsc_restricao"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["group_norm"] = df["nom_usina"].map(norm_text)
    df["is_restricted"] = df["cod_razaorestricao"].ne("") | df["cod_origemrestricao"].ne("") | df["dsc_restricao"].ne("")
    df["is_rel"] = df["cod_razaorestricao"].eq("REL")
    df["is_rel_sis"] = df["cod_razaorestricao"].eq("REL") & df["cod_origemrestricao"].eq("SIS")
    df["is_rel_loc"] = df["cod_razaorestricao"].eq("REL") & df["cod_origemrestricao"].eq("LOC")
    df["has_transmission_text"] = df["dsc_restricao"].str.contains(
        r"transmiss|flux| LT |kV|carregamento|inequa",
        case=False,
        regex=True,
        na=False,
    )
    grouped = (
        df.groupby(["id_subsistema", "id_estado", "din_instante", "group_norm"], as_index=False)
        .agg(
            matched_restricted=("is_restricted", "max"),
            matched_rel=("is_rel", "max"),
            matched_rel_sis=("is_rel_sis", "max"),
            matched_rel_loc=("is_rel_loc", "max"),
            matched_transmission_text=("has_transmission_text", "max"),
        )
    )
    return grouped


def normalize_detail(df: pd.DataFrame, technology: str, config: dict) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    optional = ["nom_conjuntousina", "nom_modalidadeoperacao", config["weather_invalid_col"]]
    for col in optional:
        if col not in df.columns:
            df[col] = ""
    required = [
        "id_subsistema",
        "id_estado",
        "nom_conjuntousina",
        "nom_usina",
        "id_ons",
        "ceg",
        "din_instante",
        config["weather_col"],
        config["weather_invalid_col"],
        "val_geracaoestimada",
        "val_geracaoverificada",
    ]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing expected detail columns {missing}")
    keep = required + ["nom_modalidadeoperacao"]
    df = df[keep].copy()
    df["technology"] = technology
    df["din_instante"] = pd.to_datetime(df["din_instante"], errors="coerce")
    df = df.dropna(subset=["din_instante"])
    for col in ["id_subsistema", "id_estado", "nom_conjuntousina", "nom_usina", "id_ons", "ceg", "nom_modalidadeoperacao"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["group_norm"] = df["nom_conjuntousina"].where(df["nom_conjuntousina"].ne(""), df["nom_usina"]).map(norm_text)
    df["weather_value_raw"] = numeric(df[config["weather_col"]])
    df["weather_invalid"] = boolish(df[config["weather_invalid_col"]])
    outside_physical_range = ~df["weather_value_raw"].between(
        config["weather_valid_min"],
        config["weather_valid_max"],
    )
    df.loc[outside_physical_range, "weather_invalid"] = True
    df["weather_value"] = df["weather_value_raw"].where(~df["weather_invalid"])
    df["estimated_mw"] = numeric(df["val_geracaoestimada"]).fillna(0.0)
    df["verified_mw"] = numeric(df["val_geracaoverificada"]).fillna(0.0)
    df["gap_mw"] = (df["estimated_mw"] - df["verified_mw"]).clip(lower=0)
    df["estimated_mwh"] = df["estimated_mw"] * 0.5
    df["verified_mwh"] = df["verified_mw"] * 0.5
    df["gap_mwh"] = df["gap_mw"] * 0.5
    df["month"] = df["din_instante"].dt.to_period("M").astype(str)
    df["year"] = df["din_instante"].dt.year
    df["hour"] = df["din_instante"].dt.hour + df["din_instante"].dt.minute / 60.0
    return df


def process_detail_file(path: Path, technology: str, config: dict) -> dict[str, pd.DataFrame | dict]:
    detail = normalize_detail(read_table(path), technology, config)
    agg_path = aggregate_path_for_detail(path, config)
    if agg_path is not None:
        aggregate = normalize_aggregate(agg_path)
        if not aggregate.empty:
            detail = detail.merge(
                aggregate,
                on=["id_subsistema", "id_estado", "din_instante", "group_norm"],
                how="left",
            )
        else:
            for col in ["matched_restricted", "matched_rel", "matched_rel_sis", "matched_rel_loc", "matched_transmission_text"]:
                detail[col] = False
    else:
        for col in ["matched_restricted", "matched_rel", "matched_rel_sis", "matched_rel_loc", "matched_transmission_text"]:
            detail[col] = False
    for col in ["matched_restricted", "matched_rel", "matched_rel_sis", "matched_rel_loc", "matched_transmission_text"]:
        detail[col] = detail[col].fillna(False).astype(bool)

    group_cols = ["technology", "month"]
    monthly = detail.groupby(group_cols, as_index=False).agg(
        rows=("id_ons", "count"),
        plants=("id_ons", "nunique"),
        estimated_mwh=("estimated_mwh", "sum"),
        verified_mwh=("verified_mwh", "sum"),
        gap_mwh=("gap_mwh", "sum"),
        weather_value_sum=("weather_value", "sum"),
        weather_valid_rows=("weather_value", "count"),
        invalid_weather_rows=("weather_invalid", "sum"),
        matched_restricted_rows=("matched_restricted", "sum"),
    )
    monthly["matched_restricted_gap_mwh"] = detail[detail["matched_restricted"]].groupby(group_cols)["gap_mwh"].sum().reindex(
        pd.MultiIndex.from_frame(monthly[group_cols]), fill_value=0
    ).to_numpy()
    monthly["matched_rel_gap_mwh"] = detail[detail["matched_rel"]].groupby(group_cols)["gap_mwh"].sum().reindex(
        pd.MultiIndex.from_frame(monthly[group_cols]), fill_value=0
    ).to_numpy()
    monthly["matched_rel_sis_gap_mwh"] = detail[detail["matched_rel_sis"]].groupby(group_cols)["gap_mwh"].sum().reindex(
        pd.MultiIndex.from_frame(monthly[group_cols]), fill_value=0
    ).to_numpy()
    monthly["matched_rel_loc_gap_mwh"] = detail[detail["matched_rel_loc"]].groupby(group_cols)["gap_mwh"].sum().reindex(
        pd.MultiIndex.from_frame(monthly[group_cols]), fill_value=0
    ).to_numpy()
    monthly["matched_transmission_text_gap_mwh"] = detail[detail["matched_transmission_text"]].groupby(group_cols)["gap_mwh"].sum().reindex(
        pd.MultiIndex.from_frame(monthly[group_cols]), fill_value=0
    ).to_numpy()

    plant_cols = ["technology", "id_subsistema", "id_estado", "nom_conjuntousina", "nom_usina", "id_ons", "ceg"]
    plant = detail.groupby(plant_cols, as_index=False).agg(
        rows=("id_ons", "count"),
        estimated_mwh=("estimated_mwh", "sum"),
        verified_mwh=("verified_mwh", "sum"),
        gap_mwh=("gap_mwh", "sum"),
        weather_value_sum=("weather_value", "sum"),
        weather_valid_rows=("weather_value", "count"),
        invalid_weather_rows=("weather_invalid", "sum"),
        matched_restricted_rows=("matched_restricted", "sum"),
    )
    plant["matched_restricted_gap_mwh"] = detail[detail["matched_restricted"]].groupby(plant_cols)["gap_mwh"].sum().reindex(
        pd.MultiIndex.from_frame(plant[plant_cols]), fill_value=0
    ).to_numpy()
    plant["matched_transmission_text_gap_mwh"] = detail[detail["matched_transmission_text"]].groupby(plant_cols)["gap_mwh"].sum().reindex(
        pd.MultiIndex.from_frame(plant[plant_cols]), fill_value=0
    ).to_numpy()

    state_cols = ["technology", "month", "id_subsistema", "id_estado"]
    state = detail.groupby(state_cols, as_index=False).agg(
        plants=("id_ons", "nunique"),
        rows=("id_ons", "count"),
        estimated_mwh=("estimated_mwh", "sum"),
        verified_mwh=("verified_mwh", "sum"),
        gap_mwh=("gap_mwh", "sum"),
        weather_value_sum=("weather_value", "sum"),
        weather_valid_rows=("weather_value", "count"),
        invalid_weather_rows=("weather_invalid", "sum"),
    )
    state["matched_restricted_gap_mwh"] = detail[detail["matched_restricted"]].groupby(state_cols)["gap_mwh"].sum().reindex(
        pd.MultiIndex.from_frame(state[state_cols]), fill_value=0
    ).to_numpy()

    weather_bins = pd.cut(detail["weather_value"], bins=config["weather_bins"], labels=config["weather_bin_labels"], include_lowest=True)
    weather = detail.assign(weather_bin=weather_bins).groupby(["technology", "weather_bin"], observed=False, as_index=False).agg(
        rows=("id_ons", "count"),
        estimated_mwh=("estimated_mwh", "sum"),
        verified_mwh=("verified_mwh", "sum"),
        gap_mwh=("gap_mwh", "sum"),
        weather_value_sum=("weather_value", "sum"),
        weather_valid_rows=("weather_value", "count"),
    )

    hour = detail.groupby(["technology", "hour"], as_index=False).agg(
        estimated_mwh=("estimated_mwh", "sum"),
        verified_mwh=("verified_mwh", "sum"),
        gap_mwh=("gap_mwh", "sum"),
        weather_value_sum=("weather_value", "sum"),
        weather_valid_rows=("weather_value", "count"),
    )

    inventory = {
        "technology": technology,
        "file_name": path.name,
        "aggregate_match_file": agg_path.name if agg_path else None,
        "rows": int(len(detail)),
        "plants": int(detail["id_ons"].nunique()),
        "start": str(detail["din_instante"].min()),
        "end": str(detail["din_instante"].max()),
        "estimated_mwh": float(detail["estimated_mwh"].sum()),
        "verified_mwh": float(detail["verified_mwh"].sum()),
        "gap_mwh": float(detail["gap_mwh"].sum()),
        "matched_restricted_gap_mwh": float(detail.loc[detail["matched_restricted"], "gap_mwh"].sum()),
    }
    return {
        "monthly": monthly,
        "plant": plant,
        "state": state,
        "weather": weather,
        "hour": hour,
        "inventory": inventory,
    }


def ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator.where(denominator.ne(0), 0) / denominator.where(denominator.ne(0), 1)


def add_gap_rates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["gap_pct_of_estimated"] = ratio(df["gap_mwh"], df["estimated_mwh"]) * 100
    if "weather_value_sum" in df.columns and "weather_valid_rows" in df.columns:
        df["avg_weather"] = ratio(df["weather_value_sum"], df["weather_valid_rows"])
    if "matched_restricted_gap_mwh" in df.columns:
        df["matched_restricted_pct_of_gap"] = ratio(df["matched_restricted_gap_mwh"], df["gap_mwh"]) * 100
    if "matched_transmission_text_gap_mwh" in df.columns:
        df["matched_transmission_text_pct_of_gap"] = ratio(df["matched_transmission_text_gap_mwh"], df["gap_mwh"]) * 100
    if "invalid_weather_rows" in df.columns and "rows" in df.columns:
        df["invalid_weather_pct"] = ratio(df["invalid_weather_rows"], df["rows"]) * 100
    return df


def save_chart(fig, path: Path) -> str:
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def make_charts(monthly: pd.DataFrame, plant_total: pd.DataFrame, weather: pd.DataFrame, state_total: pd.DataFrame) -> dict[str, str]:
    chart_dir = OUT_DIR / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    charts: dict[str, str] = {}

    pivot = monthly.pivot(index="month", columns="technology", values="gap_mwh").fillna(0)
    fig, ax = plt.subplots(figsize=(12, 5))
    pivot.plot(ax=ax, linewidth=2.2)
    ax.set_title("Plant/Weather Detail Generation Gap by Month")
    ax.set_ylabel("Estimated minus verified generation (MWh)")
    ax.grid(True, alpha=0.25)
    charts["monthly_gap"] = save_chart(fig, chart_dir / "detail_monthly_gap_mwh.png")

    pct = monthly.pivot(index="month", columns="technology", values="gap_pct_of_estimated").fillna(0)
    fig, ax = plt.subplots(figsize=(12, 5))
    pct.plot(ax=ax, linewidth=2.2)
    ax.set_title("Generation Gap as Share of Estimated Generation")
    ax.set_ylabel("% of estimated generation")
    ax.grid(True, alpha=0.25)
    charts["monthly_gap_pct"] = save_chart(fig, chart_dir / "detail_monthly_gap_pct.png")

    top = plant_total.sort_values("gap_mwh", ascending=False).head(18).copy()
    top["label"] = top["technology"] + " | " + top["id_estado"] + " | " + top["nom_usina"].str.slice(0, 38)
    fig, ax = plt.subplots(figsize=(11, 7))
    ax.barh(top["label"], top["gap_mwh"], color="#2563eb")
    ax.invert_yaxis()
    ax.set_title("Top Plant-Level Generation Gaps")
    ax.set_xlabel("Estimated minus verified generation (MWh)")
    ax.grid(True, axis="x", alpha=0.25)
    charts["top_plants"] = save_chart(fig, chart_dir / "detail_top_plants_gap_mwh.png")

    top_state = state_total.sort_values("gap_mwh", ascending=False).head(14)
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(top_state["label"], top_state["gap_mwh"], color="#16a34a")
    ax.invert_yaxis()
    ax.set_title("Top States by Plant/Weather Generation Gap")
    ax.set_xlabel("Estimated minus verified generation (MWh)")
    ax.grid(True, axis="x", alpha=0.25)
    charts["top_states"] = save_chart(fig, chart_dir / "detail_top_states_gap_mwh.png")

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax, tech in zip(axes, ["wind", "solar"]):
        sub = weather[weather["technology"].eq(tech)].copy()
        ax.bar(sub["weather_bin"].astype(str), sub["gap_pct_of_estimated"], color=("#06b6d4" if tech == "wind" else "#f59e0b"))
        ax.set_title(f"{tech.title()} gap rate by weather bin")
        ax.set_ylabel("% of estimated generation")
        ax.tick_params(axis="x", rotation=35)
        ax.grid(True, axis="y", alpha=0.25)
    charts["weather_bins"] = save_chart(fig, chart_dir / "detail_weather_bins_gap_pct.png")
    return charts


def write_report(summary: dict, charts: dict[str, str], monthly: pd.DataFrame, plant_total: pd.DataFrame, state_total: pd.DataFrame) -> None:
    monthly_table = monthly[["technology", "month", "estimated_mwh", "verified_mwh", "gap_mwh", "gap_pct_of_estimated", "matched_restricted_gap_mwh"]].tail(24).round(2).to_html(index=False, classes="table")
    plant_table = plant_total.sort_values("gap_mwh", ascending=False).head(25).round(2).to_html(index=False, classes="table")
    state_table = state_total.sort_values("gap_mwh", ascending=False).head(15).round(2).to_html(index=False, classes="table")
    html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>ONS Plant/Weather Curtailment Detail</title>
  <style>
    body {{ font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 28px; color: #111827; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(160px, 1fr)); gap: 12px; margin: 18px 0; }}
    .metric {{ border: 1px solid #d1d5db; border-radius: 8px; padding: 12px; }}
    .metric div:first-child {{ color: #6b7280; font-size: 12px; }}
    .metric div:last-child {{ font-size: 22px; font-weight: 750; margin-top: 3px; }}
    img {{ width: 100%; max-width: 1120px; border: 1px solid #e5e7eb; border-radius: 8px; margin: 10px 0 20px; }}
    .table {{ border-collapse: collapse; font-size: 12px; margin: 8px 0 24px; }}
    .table th, .table td {{ border: 1px solid #e5e7eb; padding: 5px 7px; text-align: right; }}
    .table th:first-child, .table td:first-child {{ text-align: left; }}
  </style>
</head>
<body>
  <h1>ONS Plant/Weather Curtailment Detail</h1>
  <p>Plant-level detail records for wind speed or irradiance, estimated generation, verified generation, data-quality flags, and aggregate restriction matching.</p>
  <div class="grid">
    <div class="metric"><div>Coverage</div><div>{summary['coverage_start']} to {summary['coverage_end']}</div></div>
    <div class="metric"><div>Rows processed</div><div>{summary['detail_rows_processed']:,}</div></div>
    <div class="metric"><div>Generation gap</div><div>{summary['total_gap_mwh']:,.0f} MWh</div></div>
    <div class="metric"><div>Gap rate</div><div>{summary['total_gap_pct_of_estimated']:.1f}%</div></div>
  </div>
  <h2>Monthly Detail Gap</h2>
  <img src="data:image/png;base64,{charts['monthly_gap']}">
  <h2>Monthly Gap Rate</h2>
  <img src="data:image/png;base64,{charts['monthly_gap_pct']}">
  <h2>Top Plants</h2>
  <img src="data:image/png;base64,{charts['top_plants']}">
  <h2>Top States</h2>
  <img src="data:image/png;base64,{charts['top_states']}">
  <h2>Weather Bins</h2>
  <img src="data:image/png;base64,{charts['weather_bins']}">
  <h2>Recent Monthly Data</h2>
  {monthly_table}
  <h2>Top Plant-Level Gaps</h2>
  {plant_table}
  <h2>Top State-Level Gaps</h2>
  {state_table}
</body>
</html>
"""
    (OUT_DIR / "ons_curtailment_detail_report.html").write_text(html)


def main() -> None:
    monthly_frames = []
    plant_frames = []
    state_frames = []
    weather_frames = []
    hour_frames = []
    inventory = []

    for technology, config in DATASETS.items():
        keys = list_s3_keys(config["detail_prefix"])
        detail_keys = preferred_monthly_files(keys, config["detail_stem"])
        for key in detail_keys:
            detail_path = download_key(key, config["detail_local"])
            print(f"Processing {technology} detail: {detail_path.name}")
            result = process_detail_file(detail_path, technology, config)
            monthly_frames.append(result["monthly"])
            plant_frames.append(result["plant"])
            state_frames.append(result["state"])
            weather_frames.append(result["weather"])
            hour_frames.append(result["hour"])
            inventory.append(result["inventory"])

    monthly = pd.concat(monthly_frames, ignore_index=True).groupby(["technology", "month"], as_index=False).sum(numeric_only=True)
    monthly = add_gap_rates(monthly).sort_values(["month", "technology"])

    annual = monthly.copy()
    annual["year"] = annual["month"].str[:4].astype(int)
    annual = annual.groupby(["technology", "year"], as_index=False).sum(numeric_only=True)
    annual = add_gap_rates(annual).sort_values(["year", "technology"])

    plant = pd.concat(plant_frames, ignore_index=True)
    plant_total = plant.groupby(["technology", "id_subsistema", "id_estado", "nom_conjuntousina", "nom_usina", "id_ons", "ceg"], as_index=False).sum(numeric_only=True)
    plant_total = add_gap_rates(plant_total).sort_values("gap_mwh", ascending=False)

    state = pd.concat(state_frames, ignore_index=True)
    state_monthly = state.groupby(["technology", "month", "id_subsistema", "id_estado"], as_index=False).sum(numeric_only=True)
    state_monthly = add_gap_rates(state_monthly)
    state_total = state_monthly.groupby(["id_subsistema", "id_estado"], as_index=False).sum(numeric_only=True)
    state_total = add_gap_rates(state_total)
    state_total["label"] = state_total["id_subsistema"] + " / " + state_total["id_estado"]

    weather = pd.concat(weather_frames, ignore_index=True)
    weather = weather.groupby(["technology", "weather_bin"], as_index=False, observed=False).sum(numeric_only=True)
    weather = add_gap_rates(weather)

    hour = pd.concat(hour_frames, ignore_index=True)
    hour = hour.groupby(["technology", "hour"], as_index=False).sum(numeric_only=True)
    hour = add_gap_rates(hour)

    inventory_df = pd.DataFrame(inventory)

    monthly.to_csv(OUT_DIR / "detail_monthly.csv", index=False)
    annual.to_csv(OUT_DIR / "detail_annual.csv", index=False)
    plant_total.to_csv(OUT_DIR / "detail_plant_rankings.csv", index=False)
    state_monthly.to_csv(OUT_DIR / "detail_state_monthly.csv", index=False)
    state_total.sort_values("gap_mwh", ascending=False).to_csv(OUT_DIR / "detail_state_totals.csv", index=False)
    weather.to_csv(OUT_DIR / "detail_weather_bins.csv", index=False)
    hour.to_csv(OUT_DIR / "detail_hourly_profile.csv", index=False)
    inventory_df.to_csv(OUT_DIR / "detail_file_inventory.csv", index=False)

    summary = {
        "source": "ONS AWS Open Data constrained-off detail datasets",
        "coverage_start": monthly["month"].min(),
        "coverage_end": monthly["month"].max(),
        "wind_coverage_start": monthly.loc[monthly["technology"].eq("wind"), "month"].min(),
        "wind_coverage_end": monthly.loc[monthly["technology"].eq("wind"), "month"].max(),
        "solar_coverage_start": monthly.loc[monthly["technology"].eq("solar"), "month"].min(),
        "solar_coverage_end": monthly.loc[monthly["technology"].eq("solar"), "month"].max(),
        "files_processed": int(len(inventory_df)),
        "detail_rows_processed": int(inventory_df["rows"].sum()),
        "distinct_plant_ids": int(plant_total["id_ons"].nunique()),
        "estimated_mwh": float(monthly["estimated_mwh"].sum()),
        "verified_mwh": float(monthly["verified_mwh"].sum()),
        "total_gap_mwh": float(monthly["gap_mwh"].sum()),
        "wind_gap_mwh": float(monthly.loc[monthly["technology"].eq("wind"), "gap_mwh"].sum()),
        "solar_gap_mwh": float(monthly.loc[monthly["technology"].eq("solar"), "gap_mwh"].sum()),
        "total_gap_pct_of_estimated": float(monthly["gap_mwh"].sum() / monthly["estimated_mwh"].sum() * 100),
        "matched_restricted_gap_mwh": float(monthly["matched_restricted_gap_mwh"].sum()),
        "matched_rel_gap_mwh": float(monthly["matched_rel_gap_mwh"].sum()),
        "matched_rel_sis_gap_mwh": float(monthly["matched_rel_sis_gap_mwh"].sum()),
        "matched_rel_loc_gap_mwh": float(monthly["matched_rel_loc_gap_mwh"].sum()),
        "matched_transmission_text_gap_mwh": float(monthly["matched_transmission_text_gap_mwh"].sum()),
        "method_notes": [
            "Detail files provide verified weather, estimated generation, and verified generation at half-hourly plant/conjunto resolution.",
            "gap_mwh is max(estimated generation - verified generation, 0) * 0.5 hours.",
            "Matched restricted gap joins plant/conjunto detail rows to aggregate ONS restriction records by subsystem, state, normalized conjunto name, and timestamp when possible.",
            "This detail gap is broader than official curtailment: it includes all estimated-minus-verified shortfall, while matched restriction columns isolate rows also marked in aggregate constrained-off records.",
        ],
    }
    (OUT_DIR / "detail_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    charts = make_charts(monthly, plant_total, weather, state_total)
    write_report(summary, charts, monthly, plant_total, state_total)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Outputs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
