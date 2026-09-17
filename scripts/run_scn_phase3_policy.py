from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from map_ui import add_bottom_detail_panel


ROOT = Path(__file__).resolve().parents[1]
SCN_DIR = ROOT / "clean_data" / "sovereign_compute_nexus"
PHASE2_GEOJSON = SCN_DIR / "phase2_rs" / "phase2_rs_edge_effects.geojson"
OUT_DIR = SCN_DIR / "phase3_policy"
DEFAULT_NOTES_PDF = Path("/Users/nqj5zk/Downloads/67a07e87-6be9-4994-8f8d-3a8f5dd5d7b6_526_Notes.pdf")
DEFAULT_POLICY_CORPUS = ROOT / "docs" / "phase3_redata_policy_corpus.json"


POLICY_WEIGHTS = {"Low": 1, "Medium": 5, "Critical": 100}


def extract_pdf_text(pdf_path: Path, output_txt: Path) -> str:
    if not pdf_path.exists():
        return ""
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return ""
    output_txt.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [pdftotext, "-layout", str(pdf_path), str(output_txt)],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return ""
    return output_txt.read_text(errors="ignore")


def first_snippet(text: str, terms: list[str], window: int = 220) -> dict | None:
    lower = text.lower()
    for term in terms:
        idx = lower.find(term.lower())
        if idx >= 0:
            start = max(0, idx - window // 2)
            end = min(len(text), idx + len(term) + window // 2)
            page = text[:idx].count("\f") + 1
            snippet = " ".join(text[start:end].replace("\f", " ").split())
            return {"page": page, "term": term, "snippet": snippet}
    return None


def load_policy_corpus(policy_corpus: Path) -> dict:
    if not policy_corpus.exists():
        return {
            "corpus_name": "external_policy_corpus_missing",
            "source_path": str(policy_corpus),
            "documents": [],
            "document_counts": {},
            "flags": {},
        }

    corpus = json.loads(policy_corpus.read_text())
    documents = corpus.get("documents", [])
    counts: dict[str, int] = {}
    for document in documents:
        category = str(document.get("category", "uncategorized"))
        counts[category] = counts.get(category, 0) + 1

    categories = set(counts)
    corpus["source_path"] = str(policy_corpus)
    corpus["document_counts"] = counts
    corpus["flags"] = {
        "has_redata_core_law": "redata_core_law" in categories,
        "has_redata_sustainability_regulation": "redata_sustainability_regulation" in categories,
        "has_indigenous_land_rights": "indigenous_land_rights" in categories,
        "has_consultation_rights": "consultation_rights" in categories,
        "has_marco_temporal": "marco_temporal" in categories,
        "has_protected_area_law": "protected_areas" in categories,
        "has_environmental_licensing": bool({"protected_area_licensing", "environmental_licensing"} & categories),
        "has_peer_reviewed_policy_evidence": "peer_reviewed_policy_evidence" in categories,
    }
    return corpus


def build_corpus_manifest(notes_pdf: Path, text: str, text_path: Path, policy_corpus: Path) -> dict:
    snippet_terms = {
        "sar_all_weather_monitoring": ["Synthetic Aperture Radar", "SAR penetrates cloud cover"],
        "causal_inference_required": ["Difference-in-difference", "causal discovery", "causal models"],
        "bounded_schema_required": ["strictly bounded translation schema", "categorical bins", "hard-coded mathematically"],
        "marco_temporal_precaution": ["Marco Temporal", "not yet legally homologated", "static government GIS data"],
        "participatory_mapping_boundary_gap": ["Participatory Mapping", "probabilistic risk matrices", "contested lands"],
    }
    snippets = {}
    for key, terms in snippet_terms.items():
        hit = first_snippet(text, terms)
        if hit:
            snippets[key] = hit

    flags = {
        "requires_sar": "sar_all_weather_monitoring" in snippets,
        "requires_causal_inference": "causal_inference_required" in snippets,
        "requires_bounded_schema": "bounded_schema_required" in snippets,
        "marco_temporal_precaution": "marco_temporal_precaution" in snippets,
        "participatory_mapping_boundary_gap": "participatory_mapping_boundary_gap" in snippets,
    }
    external_corpus = load_policy_corpus(policy_corpus)
    external_flags = external_corpus.get("flags", {})
    combined_flags = {
        **flags,
        **external_flags,
        "external_corpus_document_count": len(external_corpus.get("documents", [])),
    }
    return {
        "document_id": notes_pdf.name if notes_pdf.exists() else "notes_pdf_missing",
        "source_pdf": str(notes_pdf),
        "extracted_text": str(text_path) if text else None,
        "text_extracted": bool(text),
        "flags": combined_flags,
        "snippets": snippets,
        "external_policy_corpus": external_corpus,
        "schema": {
            "legal_stringency": ["Low", "Medium", "Critical"],
            "lambda_policy": POLICY_WEIGHTS,
            "llm_numeric_output_allowed": False,
        },
    }


def load_phase2() -> gpd.GeoDataFrame:
    if not PHASE2_GEOJSON.exists():
        raise SystemExit("Missing Phase 2 output. Run scripts/run_scn_phase2_rs.py first.")
    gdf = gpd.read_file(PHASE2_GEOJSON).to_crs("EPSG:4326")
    bool_cols = [
        "protected_overlap",
        "indigenous_overlap",
        "hard_exclusion",
        "outside_state_boundary",
        "boundary_fray_cell",
        "protected_fray_cell",
        "indigenous_fray_cell",
        "treatment_proxy_near_infrastructure_fray",
    ]
    for col in bool_cols:
        if col in gdf.columns:
            gdf[col] = gdf[col].astype(bool)
    return gdf


def as_float(value, default: float) -> float:
    """Coerce a row field to float, substituting ``default`` only when the value is genuinely
    missing (None / NaN / empty string).

    Not ``float(x or default)``: 0.0 is falsy, so a cell sitting exactly on a conservation-unit or
    indigenous boundary (0.0 km away) would be read as 9999 km and lose its review-buffer reason.
    The review flag itself comes from the vectorised path in classify_policy(), which was always
    correct; this only affected the human-readable policy_reason text.
    """
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if out != out else out


def reason_for_row(row: pd.Series, args: argparse.Namespace) -> str:
    reasons: list[str] = []
    if bool(row.get("protected_overlap", False)):
        reasons.append("protected_land_overlap")
    if bool(row.get("indigenous_overlap", False)):
        reasons.append("indigenous_land_overlap")
    if bool(row.get("outside_state_boundary", False)):
        reasons.append("outside_state_boundary")
    if int(row.get("dominant_lulc_class", 0)) in [26, 31, 33] or float(row.get("water_surface_share_proxy", 0) or 0) >= 0.5:
        reasons.append("surface_water_or_water_lulc")
    if float(row.get("p_deg_rs", 0) or 0) >= args.critical_pdeg:
        reasons.append("critical_rs_degradation_risk")
    elif float(row.get("p_deg_rs", 0) or 0) > args.epsilon:
        reasons.append("p_deg_rs_above_epsilon")
    if bool(row.get("boundary_fray_cell", False)):
        reasons.append("protected_or_indigenous_fray_cell")
    if as_float(row.get("nearest_constraint_km"), 9999.0) <= args.boundary_review_km:
        reasons.append("within_boundary_review_buffer")
    if bool(row.get("treatment_proxy_near_infrastructure_fray", False)):
        reasons.append("infrastructure_pressure_at_fray")
    return ";".join(dict.fromkeys(reasons)) or "no_policy_trigger"


def classify_policy(gdf: gpd.GeoDataFrame, manifest: dict, args: argparse.Namespace) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    water = (
        pd.to_numeric(gdf["dominant_lulc_class"], errors="coerce").fillna(0).astype(int).isin([26, 31, 33])
        | (pd.to_numeric(gdf["water_surface_share_proxy"], errors="coerce").fillna(0) >= 0.5)
    )
    p_deg = pd.to_numeric(gdf["p_deg_rs"], errors="coerce").fillna(1)
    nearest_constraint = pd.to_numeric(gdf["nearest_constraint_km"], errors="coerce").fillna(9999)

    legal_critical = (
        gdf["protected_overlap"].astype(bool)
        | gdf["indigenous_overlap"].astype(bool)
        | gdf["outside_state_boundary"].astype(bool)
        | water
        | (p_deg >= args.critical_pdeg)
    )

    boundary_precaution = (
        gdf["boundary_fray_cell"].astype(bool)
        | (nearest_constraint <= args.boundary_review_km)
        | gdf["treatment_proxy_near_infrastructure_fray"].astype(bool)
    )
    if manifest["flags"].get("marco_temporal_precaution", False):
        boundary_precaution = boundary_precaution | gdf["protected_fray_cell"].astype(bool) | gdf["indigenous_fray_cell"].astype(bool)

    medium = (
        boundary_precaution
        | (p_deg > args.epsilon)
        | (pd.to_numeric(gdf["rs_lulc_vulnerability"], errors="coerce").fillna(0) >= 0.70)
    )

    gdf["legal_stringency"] = np.select(
        [legal_critical, medium],
        ["Critical", "Medium"],
        default="Low",
    )
    gdf["lambda_policy"] = gdf["legal_stringency"].map(POLICY_WEIGHTS).astype(int)
    gdf["policy_hard_exclusion"] = legal_critical | (p_deg > args.epsilon)
    gdf["fsor_allowed_phase3"] = ~gdf["policy_hard_exclusion"]
    gdf["human_review_required"] = (gdf["legal_stringency"].eq("Medium")) | boundary_precaution
    gdf["policy_reason"] = gdf.apply(reason_for_row, axis=1, args=(args,))

    citation_ids = []
    for _, row in gdf.iterrows():
        ids = ["bounded_schema_required"]
        if row["boundary_fray_cell"] or row["nearest_constraint_km"] <= args.boundary_review_km:
            ids.append("marco_temporal_precaution")
            ids.append("participatory_mapping_boundary_gap")
        if row["treatment_proxy_near_infrastructure_fray"]:
            ids.append("causal_inference_required")
        if row["p_deg_rs"] > args.epsilon:
            ids.append("sar_all_weather_monitoring")
        citation_ids.append(";".join([key for key in dict.fromkeys(ids) if key in manifest["snippets"]]))
    gdf["policy_citation_ids"] = citation_ids
    gdf["phase3_status"] = "bounded_policy_classifier_complete"
    return gdf


def write_map(gdf: gpd.GeoDataFrame) -> str | None:
    try:
        import folium
    except ModuleNotFoundError:
        return None
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    center = [float(gdf["center_lat"].median()), float(gdf["center_lon"].median())]
    fmap = folium.Map(location=center, zoom_start=10, tiles="CartoDB positron", control_scale=True)
    colors = {"Low": "#2ca25f", "Medium": "#feb24c", "Critical": "#de2d26"}
    fields = [
        "h3_id",
        "legal_stringency",
        "lambda_policy",
        "fsor_allowed_phase3",
        "human_review_required",
        "p_deg_rs",
        "policy_reason",
        "policy_citation_ids",
        "nearest_constraint_name",
        "nearest_constraint_km",
    ]

    def style(feature):
        props = feature["properties"]
        stringency = props.get("legal_stringency", "Low")
        return {
            "fillColor": colors.get(stringency, "#bdbdbd"),
            "color": "#525252",
            "weight": 0.35 if stringency != "Critical" else 0.8,
            "fillOpacity": 0.50 if stringency != "Critical" else 0.62,
        }

    policy_layer = folium.GeoJson(
        gdf[fields + ["geometry"]],
        name="Phase 3 bounded policy categories",
        style_function=style,
        tooltip=folium.GeoJsonTooltip(fields=["h3_id", "legal_stringency", "p_deg_rs", "fsor_allowed_phase3"]),
    ).add_to(fmap)
    add_bottom_detail_panel(
        fmap,
        policy_layer,
        title="Phase 3 Policy Constraint Inspector",
        subtitle=(
            "Click an H3 cell to inspect legal stringency, solver eligibility, human-review triggers, "
            "and the policy reasons behind the classification."
        ),
        metric_fields=[
            "legal_stringency",
            "fsor_allowed_phase3",
            "human_review_required",
            "p_deg_rs",
            "lambda_policy",
            "nearest_constraint_km",
        ],
        detail_fields=fields,
        label_map={
            "h3_id": "H3 ID",
            "legal_stringency": "Legal Stringency",
            "lambda_policy": "Policy Weight",
            "fsor_allowed_phase3": "FSOR Allowed",
            "human_review_required": "Human Review",
            "p_deg_rs": "P_deg(x)",
            "policy_reason": "Policy Trigger Logic",
            "policy_citation_ids": "Evidence Tags",
            "nearest_constraint_name": "Nearest Constraint",
            "nearest_constraint_km": "Constraint Distance",
        },
        panel_id="phase3-policy-inspector",
    )
    folium.LayerControl(collapsed=False).add_to(fmap)
    path = OUT_DIR / "phase3_policy_map.html"
    fmap.save(path)
    return str(path)


def write_outputs(gdf: gpd.GeoDataFrame, manifest: dict, args: argparse.Namespace) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    policy_csv = OUT_DIR / "phase3_policy_penalties.csv"
    solver_csv = OUT_DIR / "phase2_3_solver_inputs.csv"
    geojson_path = OUT_DIR / "phase3_policy_penalties.geojson"
    manifest_path = OUT_DIR / "phase3_policy_corpus_manifest.json"
    summary_path = OUT_DIR / "phase3_policy_summary.json"

    gdf.to_file(geojson_path, driver="GeoJSON")
    gdf.drop(columns="geometry", errors="ignore").to_csv(policy_csv, index=False)
    solver_cols = [
        "h3_id",
        "p_deg_rs",
        "legal_stringency",
        "lambda_policy",
        "policy_hard_exclusion",
        "fsor_allowed_phase3",
        "human_review_required",
        "policy_reason",
        "policy_citation_ids",
        "boundary_fray_cell",
        "nearest_constraint_km",
    ]
    gdf[solver_cols].to_csv(solver_csv, index=False)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    map_path = write_map(gdf)

    counts = gdf["legal_stringency"].value_counts().to_dict()
    summary = {
        "case_study": "sovereign_compute_nexus_phase3_bounded_policy",
        "method_note": "This is a deterministic bounded policy classifier, not an unbounded LLM/RAG answer generator.",
        "phase2_source": str(PHASE2_GEOJSON),
        "notes_pdf": str(args.notes_pdf),
        "h3_cells": int(len(gdf)),
        "legal_stringency_counts": {k: int(v) for k, v in counts.items()},
        "policy_hard_exclusion_cells": int(gdf["policy_hard_exclusion"].sum()),
        "fsor_allowed_cells": int(gdf["fsor_allowed_phase3"].sum()),
        "human_review_cells": int(gdf["human_review_required"].sum()),
        "parameters": {
            "epsilon": args.epsilon,
            "critical_pdeg": args.critical_pdeg,
            "boundary_review_km": args.boundary_review_km,
            "policy_weights": POLICY_WEIGHTS,
        },
        "corpus_flags": manifest["flags"],
        "outputs": {
            "policy_csv": str(policy_csv),
            "solver_inputs_csv": str(solver_csv),
            "geojson": str(geojson_path),
            "interactive_map": map_path,
            "corpus_manifest": str(manifest_path),
            "summary_json": str(summary_path),
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bounded Phase 3 policy classifier for SCN H3 cells.")
    parser.add_argument("--notes-pdf", type=Path, default=DEFAULT_NOTES_PDF, help="PDF notes or legal corpus seed.")
    parser.add_argument("--epsilon", type=float, default=0.62, help="Hard Phase 4 P_deg tolerance.")
    parser.add_argument("--critical-pdeg", type=float, default=0.85, help="Critical policy threshold for RS risk.")
    parser.add_argument("--boundary-review-km", type=float, default=2.5, help="Human-review buffer around protected/indigenous constraints.")
    parser.add_argument(
        "--policy-corpus",
        type=Path,
        default=DEFAULT_POLICY_CORPUS,
        help="JSON corpus of ReData, indigenous, protected-land, licensing, and journal sources.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    text_path = OUT_DIR / "phase3_notes_extracted.txt"
    text = extract_pdf_text(args.notes_pdf, text_path)
    manifest = build_corpus_manifest(args.notes_pdf, text, text_path, args.policy_corpus)
    gdf = load_phase2()
    gdf = classify_policy(gdf, manifest, args)
    summary = write_outputs(gdf, manifest, args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    preview_cols = [
        "h3_id",
        "legal_stringency",
        "lambda_policy",
        "p_deg_rs",
        "fsor_allowed_phase3",
        "human_review_required",
        "policy_reason",
    ]
    print("\nPhase 3 policy preview")
    print(gdf.sort_values(["legal_stringency", "p_deg_rs"], ascending=[True, False])[preview_cols].head(25).to_string(index=False))


if __name__ == "__main__":
    main()
