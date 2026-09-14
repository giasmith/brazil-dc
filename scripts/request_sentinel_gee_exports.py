from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASE_STUDY_BOX = ROOT / "clean_data" / "sovereign_compute_nexus" / "ceara_case_study_box.geojson"
OUT_DIR = ROOT / "data" / "gee"
DEFAULT_DRIVE_FOLDER = "SCN_GEE_Sentinel_Ceara"
DEFAULT_EE_PROJECT = "edwc-483823"


PRODUCT_ALIASES = {
    "both": {"sentinel1", "sentinel2"},
    "all": {"sentinel1", "sentinel2"},
    "sentinel1": {"sentinel1"},
    "s1": {"sentinel1"},
    "sentinel2": {"sentinel2"},
    "s2": {"sentinel2"},
}


@dataclass
class ExportSpec:
    product: str
    dataset: str
    period_start: str
    period_end: str
    description: str
    folder: str | None
    bucket: str | None
    file_name_prefix: str
    scale_m: int
    bands: list[str]
    region_source: str


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def add_month(year: int, month: int, step: int) -> tuple[int, int]:
    month += step
    year += (month - 1) // 12
    month = ((month - 1) % 12) + 1
    return year, month


def period_ranges(start: str, end: str, frequency: str) -> list[tuple[str, str]]:
    start_date = parse_date(start)
    end_date = parse_date(end)
    if start_date >= end_date:
        raise ValueError("--start-date must be before --end-date")

    step = {"monthly": 1, "quarterly": 3, "annual": 12}[frequency]
    periods: list[tuple[str, str]] = []
    year, month = start_date.year, start_date.month
    current = date(year, month, 1)
    while current < end_date:
        next_year, next_month = add_month(current.year, current.month, step)
        nxt = min(date(next_year, next_month, 1), end_date)
        periods.append((current.isoformat(), nxt.isoformat()))
        current = nxt
    return periods


def load_region_coordinates(path: Path) -> list:
    obj = json.loads(path.read_text())
    feature = obj["features"][0]
    if feature["geometry"]["type"] != "Polygon":
        raise ValueError("Expected the case-study box GeoJSON to contain a Polygon geometry.")
    return feature["geometry"]["coordinates"]


def make_specs(args: argparse.Namespace) -> list[ExportSpec]:
    periods = period_ranges(args.start_date, args.end_date, args.frequency)
    requested_products = PRODUCT_ALIASES[args.products]
    version_suffix = f"_{args.version_tag}" if args.version_tag else ""
    specs: list[ExportSpec] = []
    for start, end in periods:
        label = f"{start[:7].replace('-', '')}_{end[:7].replace('-', '')}" if args.frequency == "monthly" else f"{start}_{end}".replace("-", "")
        if "sentinel2" in requested_products:
            specs.append(
                ExportSpec(
                    product="sentinel2_sr_harmonized",
                    dataset="COPERNICUS/S2_SR_HARMONIZED",
                    period_start=start,
                    period_end=end,
                    description=f"scn_ceara_s2_{label}{version_suffix}",
                    folder=args.drive_folder if args.export_target == "drive" else None,
                    bucket=args.gcs_bucket if args.export_target == "gcs" else None,
                    file_name_prefix=f"scn_ceara_s2_{label}{version_suffix}",
                    scale_m=args.sentinel2_scale,
                    bands=["B2", "B3", "B4", "B8", "B11", "B12", "NDVI", "NDWI", "NBR"],
                    region_source=str(CASE_STUDY_BOX),
                )
            )
        if "sentinel1" in requested_products:
            specs.append(
                ExportSpec(
                    product="sentinel1_grd",
                    dataset="COPERNICUS/S1_GRD",
                    period_start=start,
                    period_end=end,
                    description=f"scn_ceara_s1_{label}{version_suffix}",
                    folder=args.drive_folder if args.export_target == "drive" else None,
                    bucket=args.gcs_bucket if args.export_target == "gcs" else None,
                    file_name_prefix=f"scn_ceara_s1_{label}{version_suffix}",
                    scale_m=args.sentinel1_scale,
                    bands=["VV", "VH", "VV_MINUS_VH", "VV_DIV_VH"],
                    region_source=str(CASE_STUDY_BOX),
                )
            )
    return specs


def write_manifest(specs: list[ExportSpec], args: argparse.Namespace) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "case_study_box": str(CASE_STUDY_BOX),
        "status": "dry_run" if args.dry_run else "prepared_for_submission",
        "request": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "frequency": args.frequency,
            "export_target": args.export_target,
            "drive_folder": args.drive_folder if args.export_target == "drive" else None,
            "gcs_bucket": args.gcs_bucket if args.export_target == "gcs" else None,
            "ee_project": args.ee_project,
            "sentinel2_cloud_pct": args.sentinel2_cloud_pct,
            "sentinel1_orbit_pass": args.sentinel1_orbit_pass,
            "sentinel2_scale": args.sentinel2_scale,
            "sentinel1_scale": args.sentinel1_scale,
            "products": args.products,
            "version_tag": args.version_tag,
        },
        "task_count": len(specs),
        "exports": [asdict(spec) for spec in specs],
        "notes": [
            "Sentinel-2 exports use COPERNICUS/S2_SR_HARMONIZED and cloud/shadow masking from SCL.",
            "Sentinel-1 exports use COPERNICUS/S1_GRD, IW mode, VV/VH polarization, and median composites.",
            "Use monthly frequency for model training if quota allows; quarterly is a safer first export.",
            "After exports complete, place GeoTIFFs under data/gee/exports or sync from Drive/GCS before H3 extraction.",
        ],
    }
    path = OUT_DIR / "sentinel_gee_export_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return path


def submit_earth_engine_tasks(specs: list[ExportSpec], args: argparse.Namespace) -> list[dict]:
    try:
        import ee  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing Google Earth Engine Python API. Install it with:\n"
            "  python3 -m pip install earthengine-api\n\n"
            "Then authenticate:\n"
            "  earthengine authenticate\n\n"
            "After that, rerun this script without --dry-run."
        ) from exc

    if args.authenticate:
        ee.Authenticate()
    try:
        if args.ee_project:
            ee.Initialize(project=args.ee_project)
        else:
            ee.Initialize()
    except Exception as exc:
        raise SystemExit(
            "Could not initialize Earth Engine. Run `earthengine authenticate` first, "
            "or pass --authenticate / --ee-project as needed.\n"
            f"Original error: {exc}"
        ) from exc

    coords = load_region_coordinates(CASE_STUDY_BOX)
    region = ee.Geometry.Polygon(coords)

    def mask_s2_clouds(image):
        scl = image.select("SCL")
        mask = (
            scl.neq(3)
            .And(scl.neq(8))
            .And(scl.neq(9))
            .And(scl.neq(10))
            .And(scl.neq(11))
        )
        scaled = image.select(["B2", "B3", "B4", "B8", "B11", "B12"]).multiply(0.0001)
        ndvi = scaled.normalizedDifference(["B8", "B4"]).rename("NDVI")
        ndwi = scaled.normalizedDifference(["B3", "B8"]).rename("NDWI")
        nbr = scaled.normalizedDifference(["B8", "B12"]).rename("NBR")
        return scaled.addBands([ndvi, ndwi, nbr]).updateMask(mask).copyProperties(image, ["system:time_start"])

    def s2_composite(start: str, end: str):
        return (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(region)
            .filterDate(start, end)
            .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", args.sentinel2_cloud_pct))
            .map(mask_s2_clouds)
            .median()
            .select(["B2", "B3", "B4", "B8", "B11", "B12", "NDVI", "NDWI", "NBR"])
            .toFloat()
            .clip(region)
        )

    def s1_composite(start: str, end: str):
        collection = (
            ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(region)
            .filterDate(start, end)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        )
        if args.sentinel1_orbit_pass != "both":
            collection = collection.filter(ee.Filter.eq("orbitProperties_pass", args.sentinel1_orbit_pass.upper()))
        image = collection.select(["VV", "VH"]).median()
        vv = image.select("VV")
        vh = image.select("VH")
        return image.addBands(vv.subtract(vh).rename("VV_MINUS_VH")).addBands(vv.divide(vh).rename("VV_DIV_VH")).toFloat().clip(region)

    submitted = []
    for spec in specs:
        image = s2_composite(spec.period_start, spec.period_end) if spec.product.startswith("sentinel2") else s1_composite(spec.period_start, spec.period_end)
        common = {
            "image": image,
            "description": spec.description,
            "fileNamePrefix": spec.file_name_prefix,
            "region": region,
            "scale": spec.scale_m,
            "maxPixels": 1e13,
            "fileFormat": "GeoTIFF",
        }
        if args.export_target == "drive":
            task = ee.batch.Export.image.toDrive(folder=spec.folder, **common)
        else:
            task = ee.batch.Export.image.toCloudStorage(bucket=spec.bucket, **common)
        task.start()
        submitted.append({"description": spec.description, "task_id": task.id, "state": "STARTED"})
    return submitted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Request Sentinel-1 and Sentinel-2 GEE exports for the Ceará SCN case-study box.")
    parser.add_argument("--start-date", default="2021-10-01", help="Inclusive start date, YYYY-MM-DD.")
    parser.add_argument("--end-date", default="2026-05-01", help="Exclusive end date, YYYY-MM-DD.")
    parser.add_argument("--frequency", choices=["monthly", "quarterly", "annual"], default="quarterly", help="Composite/export frequency.")
    parser.add_argument("--export-target", choices=["drive", "gcs"], default="drive", help="Export destination.")
    parser.add_argument("--drive-folder", default=DEFAULT_DRIVE_FOLDER, help="Google Drive folder for Drive exports.")
    parser.add_argument("--gcs-bucket", default=None, help="Google Cloud Storage bucket for GCS exports.")
    parser.add_argument("--ee-project", default=DEFAULT_EE_PROJECT, help="Earth Engine / Google Cloud project id.")
    parser.add_argument("--authenticate", action="store_true", help="Run ee.Authenticate() before submitting tasks.")
    parser.add_argument("--dry-run", action="store_true", help="Write manifest only; do not import EE or submit tasks.")
    parser.add_argument("--products", choices=sorted(PRODUCT_ALIASES), default="both", help="Which products to request.")
    parser.add_argument("--version-tag", default="", help="Optional suffix for retry exports, e.g. v2.")
    parser.add_argument("--sentinel2-cloud-pct", type=float, default=60.0, help="Max Sentinel-2 scene cloud percentage before SCL masking.")
    parser.add_argument("--sentinel1-orbit-pass", choices=["both", "ascending", "descending"], default="both", help="Sentinel-1 orbit pass filter.")
    parser.add_argument("--sentinel2-scale", type=int, default=10, help="Sentinel-2 export scale in meters.")
    parser.add_argument("--sentinel1-scale", type=int, default=10, help="Sentinel-1 export scale in meters.")
    args = parser.parse_args()
    if args.export_target == "gcs" and not args.gcs_bucket:
        raise SystemExit("--gcs-bucket is required when --export-target gcs")
    return args


def main() -> None:
    args = parse_args()
    specs = make_specs(args)
    manifest_path = write_manifest(specs, args)
    print(f"Wrote request manifest: {manifest_path}")
    print(f"Prepared {len(specs)} export tasks.")

    if args.dry_run:
        print("Dry run only; no Earth Engine tasks submitted.")
        return

    submitted = submit_earth_engine_tasks(specs, args)
    log_bits = []
    if args.products not in {"both", "all"}:
        log_bits.append(args.products)
    if args.version_tag:
        log_bits.append(args.version_tag)
    log_suffix = "_" + "_".join(log_bits) if log_bits else ""
    submitted_path = OUT_DIR / f"sentinel_gee_submitted_tasks{log_suffix}.json"
    submitted_path.write_text(json.dumps(submitted, ensure_ascii=False, indent=2))
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "submitted"
    manifest["submitted_at_utc"] = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    manifest["submitted_task_count"] = len(submitted)
    manifest["submitted_tasks_log"] = str(submitted_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Submitted {len(submitted)} Earth Engine export tasks.")
    print(f"Task log: {submitted_path}")


if __name__ == "__main__":
    main()
