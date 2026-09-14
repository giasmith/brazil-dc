#!/usr/bin/env python3
"""Catalog and link Google Drive Desktop Sentinel exports into the project.

Earth Engine writes the Sentinel exports to Google Drive. Once Google Drive for
Desktop syncs those GeoTIFFs locally, this script creates project-local links,
records raster metadata, and reports which expected quarterly exports are still
missing.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import rasterio


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DRIVE_DIR = Path(
    "/Users/nqj5zk/Library/CloudStorage/"
    "GoogleDrive-starlab642@gmail.com/My Drive/SCN_GEE_Sentinel_Ceara"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "gee" / "exports"
CATALOG_CSV = PROJECT_ROOT / "data" / "gee" / "sentinel_drive_export_catalog.csv"
CATALOG_JSON = PROJECT_ROOT / "data" / "gee" / "sentinel_drive_export_catalog.json"
SUMMARY_JSON = PROJECT_ROOT / "data" / "gee" / "sentinel_drive_ingest_summary.json"

FILENAME_RE = re.compile(
    r"^scn_ceara_(?P<sensor>s1|s2)_(?P<start>\d{8})_(?P<end>\d{8})(?:_(?P<version>v\d+))?\.tiff?$",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drive-dir",
        type=Path,
        default=DEFAULT_DRIVE_DIR,
        help="Local Google Drive Desktop folder containing the Earth Engine GeoTIFF exports.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Project directory where symlinks or copied GeoTIFFs are placed.",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy files instead of creating symlinks. This uses much more disk space.",
    )
    parser.add_argument(
        "--refresh-links",
        action="store_true",
        help="Replace existing project links/files when their target differs.",
    )
    parser.add_argument(
        "--include-old-s2",
        action="store_true",
        help="Also ingest original non-v2 Sentinel-2 exports. Default excludes them because the first S2 batch had dtype failures.",
    )
    return parser.parse_args()


def load_expected_exports() -> list[dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}

    initial_log = PROJECT_ROOT / "data" / "gee" / "sentinel_gee_submitted_tasks.json"
    retry_log = PROJECT_ROOT / "data" / "gee" / "sentinel_gee_submitted_tasks_s2_v2.json"

    if initial_log.exists():
        for item in json.loads(initial_log.read_text()):
            description = item.get("description", "")
            if "_s1_" not in description:
                continue
            filename = f"{description}.tif"
            expected[filename] = {
                "filename": filename,
                "description": description,
                "task_id": item.get("task_id"),
                "task_state_at_submission": item.get("state"),
                "expected_source": initial_log.name,
            }

    if retry_log.exists():
        for item in json.loads(retry_log.read_text()):
            description = item.get("description", "")
            filename = f"{description}.tif"
            expected[filename] = {
                "filename": filename,
                "description": description,
                "task_id": item.get("task_id"),
                "task_state_at_submission": item.get("state"),
                "expected_source": retry_log.name,
            }

    return sorted(expected.values(), key=lambda row: row["filename"])


def parse_export_name(path: Path) -> dict[str, Any] | None:
    match = FILENAME_RE.match(path.name)
    if not match:
        return None

    sensor = match.group("sensor").lower()
    version = match.group("version") or ""
    product = "sentinel1_sar" if sensor == "s1" else "sentinel2_multispectral_indices"
    return {
        "filename": path.name,
        "sensor": sensor,
        "product": product,
        "start_date": datetime.strptime(match.group("start"), "%Y%m%d").date().isoformat(),
        "end_date": datetime.strptime(match.group("end"), "%Y%m%d").date().isoformat(),
        "version": version,
    }


def inspect_raster(path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "readable": False,
        "read_error": "",
        "crs": "",
        "width": "",
        "height": "",
        "band_count": "",
        "dtypes": "",
        "band_descriptions": "",
        "bounds_left": "",
        "bounds_bottom": "",
        "bounds_right": "",
        "bounds_top": "",
    }

    try:
        with rasterio.open(path) as src:
            metadata.update(
                {
                    "readable": True,
                    "crs": str(src.crs) if src.crs else "",
                    "width": src.width,
                    "height": src.height,
                    "band_count": src.count,
                    "dtypes": ",".join(src.dtypes),
                    "band_descriptions": ",".join(desc or "" for desc in src.descriptions),
                    "bounds_left": src.bounds.left,
                    "bounds_bottom": src.bounds.bottom,
                    "bounds_right": src.bounds.right,
                    "bounds_top": src.bounds.top,
                }
            )
    except Exception as exc:  # noqa: BLE001 - catalog should continue when one cloud file is unavailable.
        metadata["read_error"] = str(exc)

    return metadata


def place_project_file(source: Path, output_dir: Path, copy: bool, refresh: bool) -> tuple[Path, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / source.name

    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() and destination.resolve() == source.resolve():
            return destination, "existing_link"
        if not refresh:
            return destination, "exists_skipped"
        if destination.is_dir():
            raise IsADirectoryError(destination)
        destination.unlink()

    if copy:
        shutil.copy2(source, destination)
        return destination, "copied"

    destination.symlink_to(source)
    return destination, "linked"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return

    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    drive_dir = args.drive_dir.expanduser()
    output_dir = args.output_dir

    if not drive_dir.exists():
        raise FileNotFoundError(f"Drive export folder does not exist: {drive_dir}")

    expected = load_expected_exports()
    expected_names = {row["filename"] for row in expected}
    files = sorted(
        path for path in drive_dir.iterdir() if path.is_file() and path.suffix.lower() in {".tif", ".tiff"}
    )

    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for path in files:
        parsed = parse_export_name(path)
        if parsed is None:
            skipped.append({"filename": path.name, "reason": "unrecognized_name"})
            continue

        if parsed["sensor"] == "s2" and not parsed["version"] and not args.include_old_s2:
            skipped.append({"filename": path.name, "reason": "old_sentinel2_non_v2_excluded"})
            continue

        raster_meta = inspect_raster(path)
        destination, ingest_action = place_project_file(
            source=path,
            output_dir=output_dir,
            copy=args.copy,
            refresh=args.refresh_links,
        )
        row = {
            **parsed,
            "source_path": str(path),
            "project_path": str(destination),
            "file_size_bytes": path.stat().st_size,
            "ingest_action": ingest_action,
            "expected_from_gee_log": path.name in expected_names,
            **raster_meta,
        }
        rows.append(row)

    found_names = {row["filename"] for row in rows}
    missing = [row for row in expected if row["filename"] not in found_names]

    write_csv(CATALOG_CSV, rows)
    CATALOG_JSON.write_text(json.dumps(rows, indent=2))

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "drive_dir": str(drive_dir),
        "output_dir": str(output_dir),
        "mode": "copy" if args.copy else "symlink",
        "expected_file_count": len(expected),
        "drive_tif_file_count": len(files),
        "accepted_file_count": len(rows),
        "readable_file_count": sum(1 for row in rows if row["readable"]),
        "missing_expected_count": len(missing),
        "skipped_file_count": len(skipped),
        "missing_expected_files": missing,
        "skipped_files": skipped,
        "catalog_csv": str(CATALOG_CSV),
        "catalog_json": str(CATALOG_JSON),
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2))

    readme = output_dir / "README.md"
    readme.write_text(
        "# Sentinel GEE Export Links\n\n"
        "This directory contains project-local links or copies of Sentinel GeoTIFFs exported "
        "from Google Earth Engine to Google Drive Desktop.\n\n"
        f"- Source Drive folder: `{drive_dir}`\n"
        f"- Catalog CSV: `{CATALOG_CSV}`\n"
        f"- Ingest summary: `{SUMMARY_JSON}`\n\n"
        "Re-run `python3 scripts/ingest_sentinel_drive_exports.py` as additional Drive "
        "downloads finish syncing.\n"
    )

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
