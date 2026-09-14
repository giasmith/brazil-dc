from __future__ import annotations

import base64
import json
import re
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[1]
ONS_DIR = ROOT / "data" / "ons"
OUT_DIR = ROOT / "clean_data" / "ons_curtailment_history"
OUT_DIR.mkdir(parents=True, exist_ok=True)

S3_BASE = "https://ons-aws-prod-opendata.s3.amazonaws.com/"
S3_NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}

DATASETS = {
    "wind": {
        "prefix": "dataset/restricao_coff_eolica_tm/",
        "local_dir": ONS_DIR / "restricao_coff_eolica_tm",
        "stem": "RESTRICAO_COFF_EOLICA",
        "dictionary_key": "dataset/restricao_coff_eolica_tm/DicionarioDados_RestricaoContrainedoff_UsiEolicas.json",
    },
    "solar": {
        "prefix": "dataset/restricao_coff_fotovoltaica_tm/",
        "local_dir": ONS_DIR / "restricao_coff_fotovoltaica_tm",
        "stem": "RESTRICAO_COFF_FOTOVOLTAICA",
        "dictionary_key": "dataset/restricao_coff_fotovoltaica_tm/DicionarioDados_RestricaoContrainedoff_UsiFotovoltaica.json",
    },
}


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
    preferred = []
    for month in sorted(by_month):
        preferred.append(by_month[month].get("parquet") or by_month[month]["csv"])
    return preferred


def download_key(key: str, local_dir: Path) -> Path:
    local_dir.mkdir(parents=True, exist_ok=True)
    path = local_dir / Path(key).name
    if path.exists() and path.stat().st_size > 0:
        return path
    response = requests.get(S3_BASE + key, timeout=180)
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
    ).fillna(0.0)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    required = [
        "id_subsistema",
        "nom_subsistema",
        "id_estado",
        "nom_estado",
        "nom_usina",
        "id_ons",
        "ceg",
        "din_instante",
        "val_geracao",
        "val_geracaoreferenciafinal",
        "cod_razaorestricao",
        "cod_origemrestricao",
        "dsc_restricao",
    ]
    optional_blank = ["cod_razaorestricao", "cod_origemrestricao", "dsc_restricao"]
    for col in optional_blank:
        if col not in df.columns:
            df[col] = ""
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing expected columns {missing}")

    df["din_instante"] = pd.to_datetime(df["din_instante"], errors="coerce")
    for col in ["val_geracao", "val_geracaoreferencia", "val_geracaoreferenciafinal"]:
        df[col] = numeric(df[col])
    for col in ["id_subsistema", "nom_subsistema", "id_estado", "nom_estado", "nom_usina", "id_ons", "ceg", "cod_razaorestricao", "cod_origemrestricao", "dsc_restricao"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df = df.dropna(subset=["din_instante"])
    df["month"] = df["din_instante"].dt.to_period("M").astype(str)
    df["year"] = df["din_instante"].dt.year
    df["is_restricted"] = (
        df["cod_razaorestricao"].ne("")
        | df["cod_origemrestricao"].ne("")
        | df["dsc_restricao"].ne("")
    )
    df["is_rel"] = df["cod_razaorestricao"].eq("REL")
    df["is_rel_sis"] = df["cod_razaorestricao"].eq("REL") & df["cod_origemrestricao"].eq("SIS")
    df["is_rel_loc"] = df["cod_razaorestricao"].eq("REL") & df["cod_origemrestricao"].eq("LOC")
    df["has_transmission_text"] = df["dsc_restricao"].str.contains(
        r"transmiss|flux| LT |kV|carregamento|inequa",
        case=False,
        regex=True,
        na=False,
    )
    df["generated_mwh"] = df["val_geracao"] * 0.5
    # Older ONS files sometimes leave the final reference field blank. In that
    # schema, val_geracaoreferencia is the best available reference generation.
    # Only rows with an explicit ONS restriction code/description are counted as
    # curtailed; otherwise reference-minus-generation is normal forecast error,
    # not constrained-off energy.
    df["reference_generation_mw"] = df["val_geracaoreferenciafinal"].where(
        df["val_geracaoreferenciafinal"].gt(0),
        df["val_geracaoreferencia"],
    )
    df["curtailed_mw"] = (df["reference_generation_mw"] - df["val_geracao"]).clip(lower=0)
    df.loc[~df["is_restricted"], "curtailed_mw"] = 0.0
    df["curtailed_mwh"] = df["curtailed_mw"] * 0.5
    df["potential_mwh"] = df["generated_mwh"] + df["curtailed_mwh"]
    return df


def aggregate_file(path: Path, technology: str) -> dict[str, pd.DataFrame | dict]:
    df = normalize_columns(read_table(path))
    df["technology"] = technology

    sums = ["generated_mwh", "curtailed_mwh", "potential_mwh"]
    monthly = df.groupby(["technology", "month"], as_index=False)[sums].sum()
    monthly["rel_electrical_mwh"] = df[df["is_rel"]].groupby(["technology", "month"])["curtailed_mwh"].sum().reindex(
        pd.MultiIndex.from_frame(monthly[["technology", "month"]]), fill_value=0
    ).to_numpy()
    monthly["rel_sis_mwh"] = df[df["is_rel_sis"]].groupby(["technology", "month"])["curtailed_mwh"].sum().reindex(
        pd.MultiIndex.from_frame(monthly[["technology", "month"]]), fill_value=0
    ).to_numpy()
    monthly["rel_loc_mwh"] = df[df["is_rel_loc"]].groupby(["technology", "month"])["curtailed_mwh"].sum().reindex(
        pd.MultiIndex.from_frame(monthly[["technology", "month"]]), fill_value=0
    ).to_numpy()
    monthly["transmission_text_mwh"] = df[df["has_transmission_text"]].groupby(["technology", "month"])["curtailed_mwh"].sum().reindex(
        pd.MultiIndex.from_frame(monthly[["technology", "month"]]), fill_value=0
    ).to_numpy()

    state = df.groupby(["technology", "month", "id_subsistema", "nom_subsistema", "id_estado", "nom_estado"], as_index=False)[sums].sum()
    reason = df.groupby(["technology", "cod_razaorestricao", "cod_origemrestricao"], as_index=False)["curtailed_mwh"].sum()
    descriptions = (
        df[df["dsc_restricao"].ne("")]
        .groupby(["technology", "dsc_restricao"], as_index=False)["curtailed_mwh"]
        .sum()
    )
    inventory = {
        "technology": technology,
        "file_name": path.name,
        "rows": int(len(df)),
        "start": str(df["din_instante"].min()),
        "end": str(df["din_instante"].max()),
        "generated_mwh": float(df["generated_mwh"].sum()),
        "curtailed_mwh": float(df["curtailed_mwh"].sum()),
    }
    return {
        "monthly": monthly,
        "state": state,
        "reason": reason,
        "descriptions": descriptions,
        "inventory": inventory,
    }


def add_rates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["curtailment_pct_of_potential"] = np_where_divide(df["curtailed_mwh"], df["potential_mwh"]) * 100
    df["rel_electrical_pct_of_curtailment"] = np_where_divide(df.get("rel_electrical_mwh", 0), df["curtailed_mwh"]) * 100
    df["rel_sis_pct_of_curtailment"] = np_where_divide(df.get("rel_sis_mwh", 0), df["curtailed_mwh"]) * 100
    df["rel_loc_pct_of_curtailment"] = np_where_divide(df.get("rel_loc_mwh", 0), df["curtailed_mwh"]) * 100
    df["transmission_text_pct_of_curtailment"] = np_where_divide(df.get("transmission_text_mwh", 0), df["curtailed_mwh"]) * 100
    return df


def np_where_divide(numerator, denominator):
    numerator = pd.Series(numerator).astype(float)
    denominator = pd.Series(denominator).astype(float)
    return numerator.where(denominator.ne(0), 0) / denominator.where(denominator.ne(0), 1)


def save_chart(fig, path: Path) -> str:
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def make_charts(monthly: pd.DataFrame, annual: pd.DataFrame, state_total: pd.DataFrame) -> dict[str, str]:
    chart_dir = OUT_DIR / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    charts: dict[str, str] = {}

    pivot = monthly.pivot(index="month", columns="technology", values="curtailed_mwh").fillna(0)
    fig, ax = plt.subplots(figsize=(12, 5))
    pivot.plot(ax=ax, linewidth=2.2)
    ax.set_title("ONS Constrained-Off Renewable Curtailment by Month")
    ax.set_ylabel("Curtailed energy (MWh)")
    ax.set_xlabel("")
    ax.grid(True, alpha=0.25)
    charts["monthly_curtailment"] = save_chart(fig, chart_dir / "monthly_curtailment_mwh.png")

    pct = monthly.pivot(index="month", columns="technology", values="curtailment_pct_of_potential").fillna(0)
    fig, ax = plt.subplots(figsize=(12, 5))
    pct.plot(ax=ax, linewidth=2.2)
    ax.set_title("Curtailment as Share of Potential Generation")
    ax.set_ylabel("% of potential generation")
    ax.set_xlabel("")
    ax.grid(True, alpha=0.25)
    charts["monthly_curtailment_pct"] = save_chart(fig, chart_dir / "monthly_curtailment_pct.png")

    annual_pivot = annual.pivot(index="year", columns="technology", values="curtailed_mwh").fillna(0)
    fig, ax = plt.subplots(figsize=(10, 5))
    annual_pivot.plot(kind="bar", stacked=True, ax=ax)
    ax.set_title("Annual ONS Renewable Curtailment")
    ax.set_ylabel("Curtailed energy (MWh)")
    ax.set_xlabel("")
    ax.grid(True, axis="y", alpha=0.25)
    charts["annual_curtailment"] = save_chart(fig, chart_dir / "annual_curtailment_mwh.png")

    top_state = state_total.sort_values("curtailed_mwh", ascending=False).head(12)
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(top_state["label"], top_state["curtailed_mwh"], color="#2563eb")
    ax.invert_yaxis()
    ax.set_title("Top States by Total Curtailment")
    ax.set_xlabel("Curtailed energy (MWh)")
    ax.grid(True, axis="x", alpha=0.25)
    charts["top_states"] = save_chart(fig, chart_dir / "top_states_curtailment_mwh.png")
    return charts


def write_html_report(summary: dict, charts: dict[str, str], annual: pd.DataFrame, reason: pd.DataFrame, top_desc: pd.DataFrame) -> None:
    annual_table = annual.round(2).to_html(index=False, classes="table")
    reason_table = reason.sort_values("curtailed_mwh", ascending=False).head(12).round(2).to_html(index=False, classes="table")
    desc_table = top_desc.head(12).round(2).to_html(index=False, classes="table")
    html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>ONS Curtailment History</title>
  <style>
    body {{ font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 28px; color: #111827; }}
    h1, h2 {{ margin-bottom: 8px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(160px, 1fr)); gap: 12px; margin: 18px 0; }}
    .metric {{ border: 1px solid #d1d5db; border-radius: 8px; padding: 12px; }}
    .metric div:first-child {{ color: #6b7280; font-size: 12px; }}
    .metric div:last-child {{ font-size: 22px; font-weight: 750; margin-top: 3px; }}
    img {{ width: 100%; max-width: 1120px; border: 1px solid #e5e7eb; border-radius: 8px; margin: 10px 0 20px; }}
    .table {{ border-collapse: collapse; font-size: 13px; margin: 8px 0 24px; }}
    .table th, .table td {{ border: 1px solid #e5e7eb; padding: 6px 8px; text-align: right; }}
    .table th:first-child, .table td:first-child {{ text-align: left; }}
  </style>
</head>
<body>
  <h1>ONS Renewable Curtailment History</h1>
  <p>Aggregate constrained-off wind and solar records from ONS Open Data, covering all monthly files available in the public bucket at run time.</p>
  <div class="grid">
    <div class="metric"><div>Coverage</div><div>{summary['coverage_start']} to {summary['coverage_end']}</div></div>
    <div class="metric"><div>Total curtailed</div><div>{summary['total_curtailed_mwh']:,.0f} MWh</div></div>
    <div class="metric"><div>Wind curtailed</div><div>{summary['wind_curtailed_mwh']:,.0f} MWh</div></div>
    <div class="metric"><div>Solar curtailed</div><div>{summary['solar_curtailed_mwh']:,.0f} MWh</div></div>
  </div>
  <h2>Monthly Curtailment</h2>
  <img src="data:image/png;base64,{charts['monthly_curtailment']}">
  <h2>Curtailment Rate</h2>
  <img src="data:image/png;base64,{charts['monthly_curtailment_pct']}">
  <h2>Annual Curtailment</h2>
  <img src="data:image/png;base64,{charts['annual_curtailment']}">
  <h2>Top States</h2>
  <img src="data:image/png;base64,{charts['top_states']}">
  <h2>Annual Data</h2>
  {annual_table}
  <h2>Reason Codes</h2>
  {reason_table}
  <h2>Top Restriction Descriptions</h2>
  {desc_table}
</body>
</html>
"""
    (OUT_DIR / "ons_curtailment_history_report.html").write_text(html)


def main() -> None:
    all_monthly = []
    all_state = []
    all_reason = []
    all_descriptions = []
    inventory = []
    dictionaries = {}

    for technology, config in DATASETS.items():
        keys = list_s3_keys(config["prefix"])
        monthly_keys = preferred_monthly_files(keys, config["stem"])
        dict_path = download_key(config["dictionary_key"], config["local_dir"])
        dictionaries[technology] = json.loads(dict_path.read_text())

        for key in monthly_keys:
            path = download_key(key, config["local_dir"])
            print(f"Processing {technology}: {path.name}")
            agg = aggregate_file(path, technology)
            all_monthly.append(agg["monthly"])
            all_state.append(agg["state"])
            all_reason.append(agg["reason"])
            all_descriptions.append(agg["descriptions"])
            inventory.append(agg["inventory"])

    monthly = pd.concat(all_monthly, ignore_index=True)
    monthly = monthly.groupby(["technology", "month"], as_index=False).sum(numeric_only=True)
    monthly = add_rates(monthly).sort_values(["month", "technology"])

    annual = monthly.copy()
    annual["year"] = annual["month"].str[:4].astype(int)
    annual = annual.groupby(["technology", "year"], as_index=False).sum(numeric_only=True)
    annual = add_rates(annual).sort_values(["year", "technology"])

    state = pd.concat(all_state, ignore_index=True)
    state = state.groupby(["technology", "month", "id_subsistema", "nom_subsistema", "id_estado", "nom_estado"], as_index=False).sum(numeric_only=True)
    state = add_rates(state)
    state_total = state.groupby(["id_estado", "nom_estado"], as_index=False)["curtailed_mwh"].sum()
    state_total["label"] = state_total["id_estado"] + " - " + state_total["nom_estado"]

    reason = pd.concat(all_reason, ignore_index=True)
    reason = reason.groupby(["technology", "cod_razaorestricao", "cod_origemrestricao"], as_index=False)["curtailed_mwh"].sum()
    reason["pct_of_total_curtailment"] = reason["curtailed_mwh"] / reason["curtailed_mwh"].sum() * 100

    descriptions = pd.concat(all_descriptions, ignore_index=True)
    descriptions = descriptions.groupby(["technology", "dsc_restricao"], as_index=False)["curtailed_mwh"].sum()
    descriptions = descriptions.sort_values("curtailed_mwh", ascending=False)

    file_inventory = pd.DataFrame(inventory).sort_values(["technology", "file_name"])

    monthly.to_csv(OUT_DIR / "curtailment_monthly.csv", index=False)
    annual.to_csv(OUT_DIR / "curtailment_annual.csv", index=False)
    state.to_csv(OUT_DIR / "curtailment_by_state_monthly.csv", index=False)
    state_total.sort_values("curtailed_mwh", ascending=False).to_csv(OUT_DIR / "curtailment_by_state_total.csv", index=False)
    reason.sort_values("curtailed_mwh", ascending=False).to_csv(OUT_DIR / "curtailment_by_reason_origin.csv", index=False)
    descriptions.to_csv(OUT_DIR / "curtailment_top_restriction_descriptions.csv", index=False)
    file_inventory.to_csv(OUT_DIR / "curtailment_file_inventory.csv", index=False)

    summary = {
        "source": "ONS AWS Open Data constrained-off aggregate datasets",
        "coverage_start": monthly["month"].min(),
        "coverage_end": monthly["month"].max(),
        "wind_coverage_start": monthly.loc[monthly["technology"].eq("wind"), "month"].min(),
        "wind_coverage_end": monthly.loc[monthly["technology"].eq("wind"), "month"].max(),
        "solar_coverage_start": monthly.loc[monthly["technology"].eq("solar"), "month"].min(),
        "solar_coverage_end": monthly.loc[monthly["technology"].eq("solar"), "month"].max(),
        "monthly_files_processed": int(len(file_inventory)),
        "total_generated_mwh": float(monthly["generated_mwh"].sum()),
        "total_curtailed_mwh": float(monthly["curtailed_mwh"].sum()),
        "wind_curtailed_mwh": float(monthly.loc[monthly["technology"].eq("wind"), "curtailed_mwh"].sum()),
        "solar_curtailed_mwh": float(monthly.loc[monthly["technology"].eq("solar"), "curtailed_mwh"].sum()),
        "total_curtailment_pct_of_potential": float(monthly["curtailed_mwh"].sum() / monthly["potential_mwh"].sum() * 100),
        "rel_electrical_mwh": float(monthly["rel_electrical_mwh"].sum()),
        "rel_sis_mwh": float(monthly["rel_sis_mwh"].sum()),
        "rel_loc_mwh": float(monthly["rel_loc_mwh"].sum()),
        "transmission_text_mwh": float(monthly["transmission_text_mwh"].sum()),
        "reason_code_definitions": {
            "REL": "Razão de indisponibilidade externa (elétrica)",
            "CNF": "Razão de atendimento a requisitos de confiabilidade",
            "ENE": "Razão energética",
            "PAR": "Restrição indicada no parecer de acesso",
            "LOC": "Local restriction origin",
            "SIS": "Systemic restriction origin",
        },
    }
    (OUT_DIR / "curtailment_history_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    charts = make_charts(monthly, annual, state_total)
    write_html_report(summary, charts, annual, reason, descriptions)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Outputs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
