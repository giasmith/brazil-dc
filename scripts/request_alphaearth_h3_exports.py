"""Request per-H3-cell AlphaEarth (Google Satellite Embedding V1) features from Earth Engine.

Instead of exporting 64-band rasters and summarising them locally, this script builds the H3
cells for a region, sends them to Earth Engine as polygons, and asks EE to reduce the annual
embedding image over each cell. Each task writes one CSV per (year, chunk) to Google Drive:

    h3_id, A00 ... A63 (mean embedding per cell), cos_prev (mean per-pixel cosine similarity
    with the previous year; absent for the first requested year), n_pixels

Examples
    python3 scripts/request_alphaearth_h3_exports.py --dry-run                  # Ceará box, res 8
    python3 scripts/request_alphaearth_h3_exports.py --state CE --h3-resolution 7 --dry-run
    python3 scripts/request_alphaearth_h3_exports.py --ee-project studied-union-325415   # submit

Then sync the Drive folder and run scripts/ingest_alphaearth_h3.py.

Dataset: GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL, 10 m, 64 unit-length bands, one image per
calendar year from 2017 (CC-BY-4.0, "produced by Google and Google DeepMind").
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASE_STUDY_BOX = ROOT / "clean_data" / "sovereign_compute_nexus" / "ceara_case_study_box.geojson"
TERRITORIAL = ROOT / "clean_data" / "brazil_territorial_layers.gpkg"
OUT_DIR = ROOT / "data" / "gee"
DATASET = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"
BANDS = [f"A{i:02d}" for i in range(64)]
DEFAULT_DRIVE_FOLDER = "SCN_GEE_AlphaEarth"
DEFAULT_EE_PROJECT = "studied-union-325415"


def require_h3():
    try:
        import h3  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("Missing h3. Install with: python3 -m pip install h3") from exc
    if not hasattr(h3, "polygon_to_cells"):
        raise SystemExit("h3 >= 4 is required (has polygon_to_cells). Upgrade with: python3 -m pip install -U h3")
    return h3


# ------------------------------------------------------------------ region
def load_region(args: argparse.Namespace):
    """Return (label, shapely polygon or multipolygon in EPSG:4326)."""
    import geopandas as gpd

    if args.state:
        gdf = gpd.read_file(TERRITORIAL, where=f"source_layer = 'states' AND abbrev_state = '{args.state.upper()}'")
        if len(gdf) != 1:
            raise SystemExit(f"State {args.state!r} not found in {TERRITORIAL.name}")
        return f"state_{args.state.upper()}", gdf.to_crs("EPSG:4326").geometry.iloc[0]
    box = gpd.read_file(args.box).to_crs("EPSG:4326")
    return Path(args.box).stem, box.geometry.union_all() if hasattr(box.geometry, "union_all") else box.geometry.unary_union


def h3_cells(h3, geom, resolution: int) -> list[str]:
    """H3 cells covering a (multi)polygon. Cells whose centre falls inside any part are returned."""
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    cells: set[str] = set()
    for poly in polys:
        outer = [(lat, lon) for lon, lat in poly.exterior.coords]
        holes = [[(lat, lon) for lon, lat in ring.coords] for ring in poly.interiors]
        cells.update(h3.polygon_to_cells(h3.LatLngPoly(outer, *holes), resolution))
    if not cells:
        raise SystemExit("Region produced no H3 cells; is the geometry valid and in EPSG:4326?")
    return sorted(cells)


def cell_feature(h3, cell: str) -> dict:
    # cell_to_boundary returns (lat, lng); GeoJSON wants [lng, lat] and a closed ring
    ring = [[lng, lat] for lat, lng in h3.cell_to_boundary(cell)]
    ring.append(ring[0])
    return {"type": "Feature", "properties": {"h3_id": cell}, "geometry": {"type": "Polygon", "coordinates": [ring]}}


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield i // size, items[i : i + size]


# ---------------------------------------------------------------- earth engine
def submit(cells: list[str], args: argparse.Namespace, label: str) -> list[dict]:
    try:
        import ee  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("Missing earthengine-api. Install with: python3 -m pip install earthengine-api") from exc
    h3 = require_h3()
    if args.authenticate:
        ee.Authenticate()
    ee.Initialize(project=args.ee_project) if args.ee_project else ee.Initialize()

    col = ee.ImageCollection(DATASET)

    def year_image(year: int, region):
        # The collection holds one image per UTM zone. `.first()` would pick an arbitrary zone (often far
        # from Brazil) and the reduction would run in an invalid projection, so mosaic the tiles that
        # intersect this chunk instead.
        return col.filterDate(f"{year}-01-01", f"{year + 1}-01-01").filterBounds(region).mosaic().select(BANDS)

    first_year = min(args.years)
    for year in sorted(set(args.years)):
        if col.filterDate(f"{year}-01-01", f"{year + 1}-01-01").size().getInfo() == 0:
            raise SystemExit(f"{DATASET} has no image for {year}; drop it from --years")
    mean_names = [f"{b}_mean" for b in BANDS] + ["cos_prev_mean", "A00_count"]
    out_names = ["h3_id"] + BANDS + ["cos_prev", "n_pixels"]
    reducer = ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True)

    submitted = []
    for chunk_idx, chunk in chunks(cells, args.chunk_size):
        fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Polygon(f["geometry"]["coordinates"]), f["properties"])
                                   for f in (cell_feature(h3, c) for c in chunk)])
        region = fc.geometry().bounds()
        for year in args.years:
            img = year_image(year, region)
            if year > first_year:
                # per-pixel cosine similarity with the previous year = dot product of two unit vectors
                cos_prev = img.multiply(year_image(year - 1, region)).reduce(ee.Reducer.sum())
            else:
                cos_prev = ee.Image.constant(0).updateMask(ee.Image.constant(0))  # fully masked -> null
            img = img.addBands(cos_prev.rename("cos_prev"))
            table = img.reduceRegions(collection=fc, reducer=reducer, scale=args.scale, crs="EPSG:4326", tileScale=args.tile_scale)
            desc = f"aef_{label}_r{args.h3_resolution}_{year}_c{chunk_idx:03d}"
            task = ee.batch.Export.table.toDrive(
                collection=table.select(["h3_id"] + mean_names, out_names, retainGeometry=False),
                description=desc, folder=args.drive_folder, fileNamePrefix=desc, fileFormat="CSV",
            )
            task.start()
            submitted.append({"description": desc, "task_id": task.id, "year": year, "chunk": chunk_idx, "cells": len(chunk)})
            print(f"  started {desc} ({len(chunk)} cells)")
    return submitted


# ------------------------------------------------------------------------ main
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--box", default=str(CASE_STUDY_BOX), help="GeoJSON polygon region (default: Ceará case-study box)")
    g.add_argument("--state", help="Two-letter state code from the territorial layer, e.g. CE")
    p.add_argument("--h3-resolution", type=int, default=8)
    p.add_argument("--years", type=int, nargs="+", default=[2021, 2022, 2023, 2024], help="Calendar years (dataset starts 2017)")
    p.add_argument("--scale", type=int, default=10, help="Reduction scale in metres; 10 is native, 30 is ~9x cheaper")
    p.add_argument("--chunk-size", type=int, default=2000, help="Cells per export task (inline geometry limit)")
    p.add_argument("--tile-scale", type=int, default=4, help="EE tileScale; raise if tasks fail with memory errors")
    p.add_argument("--drive-folder", default=DEFAULT_DRIVE_FOLDER)
    p.add_argument("--ee-project", default=DEFAULT_EE_PROJECT)
    p.add_argument("--authenticate", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="Write the cell list and manifest; do not touch Earth Engine")
    a = p.parse_args()
    if not 0 <= a.h3_resolution <= 15:
        p.error("--h3-resolution must be 0..15")
    if min(a.years) < 2017:
        p.error("AlphaEarth annual embeddings start in 2017")
    if a.chunk_size > 5000:
        p.error("--chunk-size above 5000 usually exceeds EE's inline request size; upload an asset instead")
    return a


def main() -> int:
    args = parse_args()
    h3 = require_h3()
    label, geom = load_region(args)
    cells = h3_cells(h3, geom, args.h3_resolution)
    n_chunks = -(-len(cells) // args.chunk_size)
    n_tasks = n_chunks * len(args.years)
    print(f"{label}: {len(cells):,} H3 cells at resolution {args.h3_resolution} -> {n_chunks} chunks x {len(args.years)} years = {n_tasks} tasks")
    if n_tasks > 300:
        print("  WARNING: EE queues ~3000 tasks per user but runs a few at a time; consider a coarser resolution, fewer years, or an uploaded asset.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cells_path = OUT_DIR / f"aef_cells_{label}_r{args.h3_resolution}.json"
    cells_path.write_text(json.dumps(cells))
    manifest = {
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "dataset": DATASET, "bands": BANDS, "region": label, "h3_resolution": args.h3_resolution,
        "cells": len(cells), "cells_file": str(cells_path.relative_to(ROOT)), "years": args.years,
        "scale_m": args.scale, "chunk_size": args.chunk_size, "drive_folder": args.drive_folder,
        "status": "dry_run" if args.dry_run else "submitted", "tasks": [],
        "notes": [
            "Each CSV row is one H3 cell: mean of the 64 unit-length embedding bands, mean per-pixel cosine similarity to the previous year (cos_prev), pixel count.",
            "Means of unit vectors are not unit length; ingest_alphaearth_h3.py renormalises them.",
            "Attribution: AlphaEarth Foundations Satellite Embedding dataset, Google and Google DeepMind, CC-BY-4.0.",
        ],
    }
    if not args.dry_run:
        manifest["tasks"] = submit(cells, args, label)
    path = OUT_DIR / f"aef_export_manifest_{label}_r{args.h3_resolution}.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
