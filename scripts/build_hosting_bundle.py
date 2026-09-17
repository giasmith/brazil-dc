"""Build the static hosting bundle: state-partitioned GeoParquet, tables, PMTiles and a manifest.

    python3 scripts/build_hosting_bundle.py              # everything
    python3 scripts/build_hosting_bundle.py --no-tiles   # skip PMTiles (tippecanoe not installed)
    python3 scripts/build_hosting_bundle.py --layers states,ons_buses

Output (gitignored, upload with `huggingface-cli upload`, see hosting/README.md):

    hosting/
      README.md                            dataset card (copied from docs/hosting_dataset_card.md)
      manifest.json                        every layer: path, rows, bbox, source, license
      geoparquet/<layer>/uf=<UF>/part.parquet   national vector layers, EPSG:4326, hive-partitioned by state
      tables/<name>.parquet                 non-spatial tables
      pmtiles/brazil_dc.pmtiles             all vector layers as one tileset (needs tippecanoe)

Every feature gets a two-letter `uf`. Where the source column is missing, blank, or a full state
name (the protected-lands layer), the state is assigned by spatial join of the feature's
representative point against the IBGE state polygons. Features outside every state (offshore
wind, bad coordinates) land in `uf=XX` so nothing is silently dropped.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.dataset as ds
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "clean_data"
OUT = ROOT / "hosting"
SCN = CLEAN / "sovereign_compute_nexus"
TERRITORIAL = CLEAN / "brazil_territorial_layers.gpkg"
INTEGRATED = CLEAN / "integrated_dc_investment" / "brazil_integrated_dc_layers.gpkg"
CRS = "EPSG:4326"
UNKNOWN_UF = "XX"
VERSION = "v1"

# name -> reader spec, candidate uf columns, source, license
VECTOR_LAYERS: dict[str, dict] = {
    "states": dict(src=TERRITORIAL, where="source_layer = 'states'", uf_cols=["abbrev_state"], source="IBGE via IPEA geobr 2025", license="CC-BY-4.0"),
    "municipalities": dict(src=TERRITORIAL, where="source_layer = 'municipalities'", uf_cols=["abbrev_state"], source="IBGE via IPEA geobr 2025", license="CC-BY-4.0"),
    "protected_lands": dict(src=INTEGRATED, layer="protected_lands", uf_cols=[], source="MMA/CNUC via IPEA geobr 2025-03", license="CC-BY-4.0"),  # 'uf' holds full state names
    "indigenous_lands": dict(src=INTEGRATED, layer="indigenous_lands", uf_cols=["abbrev_state"], source="FUNAI via IPEA geobr 2025", license="CC-BY-4.0"),
    "ons_buses": dict(src=INTEGRATED, layer="ons_buses", uf_cols=["id_estado"], source="ONS open data (subestacao)", license="ONS open data terms"),
    "ons_branches": dict(src=INTEGRATED, layer="ons_branches_with_scenario_stress", uf_cols=["from_state"], source="ONS open data (linha_transmissao) + DC power-flow scenario", license="ONS open data terms"),
    "gem_current_generators": dict(src=INTEGRATED, layer="gem_current_generators", uf_cols=["state_code"], source="Global Energy Monitor", license="CC-BY-4.0"),
    "gem_proposed_generators": dict(src=INTEGRATED, layer="gem_proposed_generators", uf_cols=["state_code"], source="Global Energy Monitor", license="CC-BY-4.0"),
    "data_centers": dict(src=INTEGRATED, layer="idc_points", uf_cols=["state"], source="PeeringDB public facility file + OpenStreetMap", license="PeeringDB terms; OSM ODbL"),
    "state_suitability": dict(src=INTEGRATED, layer="state_suitability", uf_cols=["abbrev_state"], source="derived (this pipeline)", license="CC-BY-4.0"),
    "h3_phase4_scored": dict(src=SCN / "phase4_optimization" / "phase4_all_h3_scored.geojson", uf_cols=["state_code"], source="derived (Phase 4, Ceará case study only)", license="CC-BY-4.0"),
    "h3_phase4_pareto": dict(src=SCN / "phase4_optimization" / "phase4_pareto_frontier.geojson", uf_cols=["state_code"], source="derived (Phase 4, Ceará case study only)", license="CC-BY-4.0"),
}

TABLES: dict[str, dict] = {
    "curtailment_by_state_monthly": dict(src=CLEAN / "ons_curtailment_history" / "curtailment_by_state_monthly.csv", source="ONS restricao_coff_*", license="ONS open data terms"),
    "curtailment_by_state_total": dict(src=CLEAN / "ons_curtailment_history" / "curtailment_by_state_total.csv", source="ONS restricao_coff_*", license="ONS open data terms"),
    "curtailment_monthly": dict(src=CLEAN / "ons_curtailment_history" / "curtailment_monthly.csv", source="ONS restricao_coff_*", license="ONS open data terms"),
    "state_suitability_table": dict(src=CLEAN / "integrated_dc_investment" / "brazil_state_dc_investor_suitability.csv", source="derived (this pipeline)", license="CC-BY-4.0"),
    "phase4_recommended_sites": dict(src=SCN / "phase4_optimization" / "phase4_recommended_sites.csv", source="derived (Phase 4, Ceará)", license="CC-BY-4.0"),
    "ons_inventory": dict(src=ROOT / "data" / "ons" / "ons_inventory.json", source="this pipeline", license="CC-BY-4.0"),
}


# ------------------------------------------------------------------ helpers
def log(msg: str) -> None:
    print(msg, flush=True)


def read_vector(spec: dict) -> gpd.GeoDataFrame:
    src: Path = spec["src"]
    if not src.exists():
        raise FileNotFoundError(src)
    kwargs = {}
    if "layer" in spec:
        kwargs["layer"] = spec["layer"]
    if "where" in spec:
        kwargs["where"] = spec["where"]
    try:
        gdf = gpd.read_file(src, **kwargs)
    except (TypeError, ValueError) as exc:  # older geopandas/fiona: no `where`
        if "where" not in kwargs:
            raise
        log(f"    ({exc.__class__.__name__}: filtering in pandas instead of OGR)")
        kwargs.pop("where")
        gdf = gpd.read_file(src, **kwargs)
        gdf = gdf[gdf["source_layer"].eq(spec["where"].split("'")[1])]
    if gdf.crs is None:
        log(f"    WARNING no CRS on {src.name}; assuming {CRS}")
        gdf = gdf.set_crs(CRS)
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(CRS)
    # drop GPKG bookkeeping and normalise the geometry column name
    gdf = gdf.drop(columns=[c for c in ("fid",) if c in gdf.columns])
    if gdf.geometry.name != "geometry":
        gdf = gdf.rename_geometry("geometry")
    n_before = len(gdf)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    if len(gdf) != n_before:
        log(f"    dropped {n_before - len(gdf)} rows with empty geometry")
    return gdf


def load_states() -> gpd.GeoDataFrame:
    states = read_vector(VECTOR_LAYERS["states"])[["abbrev_state", "geometry"]].rename(columns={"abbrev_state": "_uf_sj"})
    if len(states) != 27:
        raise RuntimeError(f"expected 27 states, got {len(states)}")
    return states


def assign_uf(gdf: gpd.GeoDataFrame, uf_cols: list[str], states: gpd.GeoDataFrame, name: str) -> gpd.GeoDataFrame:
    """Two-letter state per feature: trusted column first, spatial join for the rest, XX if outside Brazil."""
    gdf = gdf.copy()
    uf = pd.Series(pd.NA, index=gdf.index, dtype="string")
    for col in uf_cols:
        if col in gdf.columns:
            cand = gdf[col].astype("string").str.strip().str.upper()
            ok = cand.str.fullmatch(r"[A-Z]{2}").fillna(False)
            uf = uf.where(uf.notna(), cand.where(ok))
    missing = uf.isna()
    if missing.any():
        pts = gdf.loc[missing, ["geometry"]].copy()
        pts["geometry"] = pts.geometry.representative_point()
        joined = gpd.sjoin(pts, states, how="left", predicate="within")
        joined = joined[~joined.index.duplicated(keep="first")]  # a point on a border matches twice
        uf.loc[missing] = joined["_uf_sj"].astype("string")
        n_fixed = int(uf.loc[missing].notna().sum())
        log(f"    {name}: {int(missing.sum())} features without a usable state code; {n_fixed} resolved by spatial join")
    gdf["uf"] = uf.fillna(UNKNOWN_UF)
    n_unknown = int((gdf["uf"] == UNKNOWN_UF).sum())
    if n_unknown:
        log(f"    {name}: {n_unknown} features outside every state -> uf={UNKNOWN_UF}")
    return gdf


def write_partitioned(gdf: gpd.GeoDataFrame, name: str) -> dict:
    out_dir = OUT / "geoparquet" / name
    if out_dir.exists():
        shutil.rmtree(out_dir)
    parts = {}
    for uf, sub in gdf.groupby("uf", sort=True):
        part_dir = out_dir / f"uf={uf}"
        part_dir.mkdir(parents=True)
        sub.drop(columns=["uf"]).to_parquet(part_dir / "part.parquet", index=False, compression="zstd", schema_version="1.0.0")
        parts[uf] = len(sub)
    # verify: hive partitioning reconstructs uf, totals match, GeoParquet metadata present
    dataset = ds.dataset(out_dir, format="parquet", partitioning="hive")
    total = dataset.count_rows()
    if total != len(gdf):
        raise RuntimeError(f"{name}: wrote {total} rows, expected {len(gdf)}")
    first = next(out_dir.rglob("part.parquet"))
    if b"geo" not in pq.read_schema(first).metadata:
        raise RuntimeError(f"{name}: missing GeoParquet 'geo' metadata")
    minx, miny, maxx, maxy = gdf.total_bounds
    return {
        "path": f"geoparquet/{name}",
        "layout": "hive: uf=<state>/part.parquet",
        "rows": int(total),
        "partitions": parts,
        "geometry_type": sorted(gdf.geom_type.unique().tolist()),
        "bbox": [round(float(v), 6) for v in (minx, miny, maxx, maxy)],
        "columns": [c for c in gdf.columns if c not in ("geometry", "uf")],
    }


def write_table(name: str, spec: dict) -> dict:
    src: Path = spec["src"]
    df = pd.read_json(src) if src.suffix == ".json" else pd.read_csv(src)
    out = OUT / "tables" / f"{name}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False, compression="zstd")
    back = pq.ParquetFile(str(out)).metadata.num_rows
    if back != len(df):
        raise RuntimeError(f"{name}: wrote {back} rows, expected {len(df)}")
    return {"path": f"tables/{name}.parquet", "rows": int(back), "columns": list(df.columns)}


def build_tiles(layers: dict[str, gpd.GeoDataFrame]) -> dict | None:
    exe = shutil.which("tippecanoe")
    if not exe:
        log("  tippecanoe not found; skipping PMTiles. Install with:  brew install tippecanoe")
        return None
    out = OUT / "pmtiles" / "brazil_dc.pmtiles"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [exe, "-o", str(out), "--force", "--projection=EPSG:4326",
               "--drop-densest-as-needed", "--extend-zooms-if-still-dropping", "--no-tile-size-limit", "-Z3", "-z12"]
        for name, gdf in layers.items():
            path = Path(tmp) / f"{name}.geojsonl"
            # newline-delimited GeoJSON streams; avoids building one giant FeatureCollection in memory
            with path.open("w") as fh:
                for feat in gdf.iterfeatures(na="null", drop_id=True):
                    fh.write(json.dumps(feat, ensure_ascii=False, default=str) + "\n")
            cmd += ["-L", f"{name}:{path}"]
        log(f"  tippecanoe: {len(layers)} layers -> {out.relative_to(ROOT)}")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"tippecanoe failed:\n{res.stderr[-2000:]}")
    return {"path": "pmtiles/brazil_dc.pmtiles", "bytes": out.stat().st_size, "layers": list(layers), "minzoom": 3, "maxzoom": 12}


# --------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-tiles", action="store_true")
    ap.add_argument("--layers", help="comma-separated subset of vector layers")
    args = ap.parse_args()
    wanted = set(args.layers.split(",")) if args.layers else set(VECTOR_LAYERS)
    unknown = wanted - set(VECTOR_LAYERS)
    if unknown:
        ap.error(f"unknown layers {sorted(unknown)}; choose from {sorted(VECTOR_LAYERS)}")

    OUT.mkdir(exist_ok=True)
    manifest = {"version": VERSION, "built": date.today().isoformat(), "crs": CRS, "unknown_uf": UNKNOWN_UF,
                "vector_layers": {}, "tables": {}, "pmtiles": None}

    log("[1/3] Vector layers -> GeoParquet")
    states = load_states()
    built: dict[str, gpd.GeoDataFrame] = {}
    for name, spec in VECTOR_LAYERS.items():
        if name not in wanted:
            continue
        log(f"  {name}")
        gdf = assign_uf(read_vector(spec), spec["uf_cols"], states, name)
        info = write_partitioned(gdf, name)
        info.update(source=spec["source"], license=spec["license"])
        manifest["vector_layers"][name] = info
        built[name] = gdf
        log(f"    {info['rows']:,} rows, {len(info['partitions'])} partitions")

    log("[2/3] Tables -> Parquet")
    for name, spec in TABLES.items():
        if not spec["src"].exists():
            log(f"  skip {name} (missing {spec['src'].relative_to(ROOT)})")
            continue
        info = write_table(name, spec)
        info.update(source=spec["source"], license=spec["license"])
        manifest["tables"][name] = info
        log(f"  {name}: {info['rows']:,} rows")

    log("[3/3] PMTiles")
    if not args.no_tiles and built:
        manifest["pmtiles"] = build_tiles(built)

    card = ROOT / "docs" / "hosting_dataset_card.md"
    if card.exists():
        shutil.copyfile(card, OUT / "README.md")  # becomes the Hugging Face dataset card
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    total_bytes = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    log(f"\nwrote {OUT.relative_to(ROOT)}/manifest.json; bundle size {total_bytes / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
