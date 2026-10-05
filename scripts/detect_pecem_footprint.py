"""Look for the Pecém (Omnia / "TikTok") data-center construction footprint with Sentinel-2.

Searches the inferred candidate parcels in analysis/pecem_dc_location/pecem_candidate_parcels.geojson
(buffered) for land that went from vegetated/undisturbed to bare or built between a BEFORE window
(default Jul-Dec 2025; press reports say construction began Jan 2026) and an AFTER window (default
the last ~3 months).

Division of labour (keeps every Earth Engine request small enough to run synchronously):
    Earth Engine : two cloud-masked median composites, downloaded as small GeoTIFFs (~10 m, UTM 24S)
    local        : change rule, speckle cleaning, blob labelling, vectorising, statistics, previews
So after the first run you can re-tune thresholds with --reuse and never touch EE again.

Outputs (default dir analysis/pecem_dc_location/footprint/):
    before_s2.tif / after_s2.tif  composites: B4, B3, B2, NDVI, NDBI, BSI (float32, nodata -9999)
    detected_footprints.geojson   change polygons >= --min-ha: area_ha, centroid, NDVI/NDBI before & after,
                                  which candidate parcel(s) they touch
    summary.json                  windows, scene counts, thresholds, totals
    before_rgb.png / after_rgb.png / change_overlay.png   quick-look previews
    detail_fid<N>.png             zoomed before/after pair for each detected polygon
    ndvi_timeseries.csv / .png    (--timeseries) monthly mean NDVI per detected polygon, to date the clearing

Examples
    .venv/bin/python scripts/detect_pecem_footprint.py --timeseries
    .venv/bin/python scripts/detect_pecem_footprint.py --reuse --min-ha 3 --ndvi-drop 0.10   # re-tune, no EE
    .venv/bin/python scripts/detect_pecem_footprint.py --after 2026-04-01 2026-07-01
    .venv/bin/python scripts/detect_pecem_footprint.py --search some_other_polygon.geojson --scale 20

Needs: earthengine-api, requests, rasterio, scipy, shapely, pyproj, matplotlib (all in .venv).
Caveats: Ceará's rainy season (roughly Feb-May) can leave a composite patchy; if scenes_after is low or the
PNGs show holes, widen --after. The change rule is a heuristic for "cleared or built"; treat polygons as
leads to confirm in the PNGs, not as a surveyed footprint.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEARCH = ROOT / "analysis" / "pecem_dc_location" / "pecem_candidate_parcels.geojson"
DEFAULT_OUT = ROOT / "analysis" / "pecem_dc_location" / "footprint"
DEFAULT_EE_PROJECT = "studied-union-325415"
S2 = "COPERNICUS/S2_SR_HARMONIZED"
S2_BANDS = ["B2", "B3", "B4", "B8", "B11", "B12"]
OUT_BANDS = ["B4", "B3", "B2", "NDVI", "NDBI", "BSI"]  # order inside the GeoTIFFs
UTM = "EPSG:32724"  # UTM zone 24S, same as the CIPP Plano Diretor
NODATA = -9999.0
MAX_DOWNLOAD_BYTES = 45_000_000  # EE's synchronous download cap is ~48 MB


# ------------------------------------------------------------------ geometry helpers (local)
def wgs_to_utm():
    from pyproj import Transformer

    return Transformer.from_crs("EPSG:4326", UTM, always_xy=True)


def utm_to_wgs():
    from pyproj import Transformer

    return Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)


def reproject(geom, transformer):
    from shapely.ops import transform

    return transform(transformer.transform, geom)


def load_parcels(path: Path):
    """[(id, shapely geometry in EPSG:4326)] from a GeoJSON Feature or FeatureCollection."""
    from shapely.geometry import shape

    data = json.loads(path.read_text())
    feats = data["features"] if data.get("type") == "FeatureCollection" else [data]
    if not feats:
        raise SystemExit(f"{path} has no features")
    out = []
    for i, f in enumerate(feats):
        pid = (f.get("properties") or {}).get("id") or f"parcel_{i + 1}"
        g = shape(f["geometry"])
        if g.is_empty:
            raise SystemExit(f"{path}: feature {pid} has an empty geometry")
        out.append((pid, g.buffer(0)))  # buffer(0) repairs self-touching rings from raster tracing
    return out


# ------------------------------------------------------------------ earth engine
def init_ee(project: str | None):
    try:
        import ee  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("Missing earthengine-api. Install with: uv add earthengine-api") from exc
    try:
        ee.Initialize(project=project) if project else ee.Initialize()
    except Exception:  # noqa: BLE001 - EE raises its own exception types
        ee.Authenticate()
        ee.Initialize(project=project) if project else ee.Initialize()
    return ee


def mask_and_index(ee, img):
    """Same SCL mask as gee/request_sentinel_ceara_exports.js, plus NDVI, NDBI and a bare-soil index."""
    scl = img.select("SCL")
    clear = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
    s = img.select(S2_BANDS).multiply(1e-4)
    ndvi = s.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndbi = s.normalizedDifference(["B11", "B8"]).rename("NDBI")
    bsi = s.expression(
        "((B11 + B4) - (B8 + B2)) / ((B11 + B4) + (B8 + B2))",
        {b: s.select(b) for b in ("B2", "B4", "B8", "B11")},
    ).rename("BSI")
    return s.addBands([ndvi, ndbi, bsi]).updateMask(clear).copyProperties(img, ["system:time_start"])


def composite(ee, region, start: str, end: str, cloud_pct: int, max_scenes: int):
    """Median of the clearest <= max_scenes scenes. Returns (image, scenes_available, scenes_used)."""
    col = (
        ee.ImageCollection(S2)
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", cloud_pct))
    )
    n = col.size().getInfo()
    if n == 0:
        raise SystemExit(f"No Sentinel-2 scenes for {start}..{end} with cloud <= {cloud_pct}%. Widen the window.")
    col = col.sort("CLOUDY_PIXEL_PERCENTAGE").limit(max_scenes)
    img = col.map(lambda i: mask_and_index(ee, i)).median().select(OUT_BANDS).unmask(NODATA).toFloat().clip(region)
    return img, n, min(n, max_scenes)


def download_tif(ee, img, region, scale: float, path: Path):
    import requests

    url = img.getDownloadURL({"region": region, "scale": scale, "crs": UTM, "format": "GEO_TIFF"})
    r = requests.get(url, timeout=600)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "tiff" not in ctype and not r.content[:4] in (b"II*\x00", b"MM\x00*"):
        raise SystemExit(f"Earth Engine did not return a GeoTIFF ({ctype}): {r.text[:300]}")
    path.write_bytes(r.content)
    return len(r.content)


# ------------------------------------------------------------------ local raster work
def read_tif(path: Path):
    import rasterio

    with rasterio.open(path) as ds:
        arr = ds.read().astype("float32")
        tr = ds.transform
        if ds.count != len(OUT_BANDS):
            raise SystemExit(f"{path.name}: expected {len(OUT_BANDS)} bands, found {ds.count}")
    arr[arr <= NODATA + 1] = np.nan
    return dict(zip(OUT_BANDS, arr)), tr


def rasterize(geoms, shape, tr):
    from rasterio import features

    return features.rasterize([(g, 1) for g in geoms], out_shape=shape, transform=tr, fill=0, dtype="uint8").astype(bool)


def change_mask(b: dict, a: dict, inside: np.ndarray, args) -> np.ndarray:
    """Boolean raster of pixels that went from vegetated/undisturbed to bare or built."""
    from scipy import ndimage as ndi

    with np.errstate(invalid="ignore"):
        valid = inside & np.isfinite(b["NDVI"]) & np.isfinite(a["NDVI"]) & np.isfinite(b["NDBI"]) & np.isfinite(a["NDBI"])
        low_veg_now = a["NDVI"] < args.ndvi_after_max
        cleared = low_veg_now & ((b["NDVI"] - a["NDVI"]) > args.ndvi_drop)
        built = low_veg_now & (((a["NDBI"] - b["NDBI"]) > args.ndbi_rise) | ((a["BSI"] - b["BSI"]) > args.bsi_rise))
        changed = (cleared | built) & valid
    k = np.ones((3, 3), bool)
    changed = ndi.binary_opening(changed, structure=k)  # drop isolated pixels and 1-px strings
    changed = ndi.binary_closing(changed, structure=k)  # fill pinholes inside blobs
    return changed


def vectorize(changed: np.ndarray, b: dict, a: dict, tr, scale: float, parcels_utm, args):
    from rasterio import features
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union
    from scipy import ndimage as ndi

    labels, n = ndi.label(changed, structure=np.ones((3, 3), bool))  # 8-connected blobs
    if n == 0:
        return [], np.zeros_like(changed), {}
    px_ha = scale * scale / 1e4
    sizes = np.bincount(labels.ravel())[1:]  # pixel count per label 1..n
    keep_ids = [i for i, s in enumerate(sizes, start=1) if s * px_ha >= args.min_ha]
    keep = np.isin(labels, keep_ids)
    polys: dict[int, list] = {}
    for geom, val in features.shapes(labels.astype("int32"), mask=keep, connectivity=8, transform=tr):
        polys.setdefault(int(val), []).append(shape(geom))
    to_wgs = utm_to_wgs()
    feats = []
    geoms_utm: dict[int, object] = {}
    for lab_id, parts in polys.items():
        g_utm = unary_union(parts).buffer(0)
        geoms_utm[lab_id] = g_utm
        idx = labels == lab_id
        c = g_utm.centroid
        lon, lat = to_wgs.transform(c.x, c.y)
        touches = [pid for pid, pg in parcels_utm if pg.intersects(g_utm)]
        props = {
            "fid": lab_id,
            "area_ha": round(g_utm.area / 1e4, 2),
            "lon": round(lon, 5),
            "lat": round(lat, 5),
            "ndvi_before": round(float(np.nanmean(b["NDVI"][idx])), 3),
            "ndvi_after": round(float(np.nanmean(a["NDVI"][idx])), 3),
            "ndbi_before": round(float(np.nanmean(b["NDBI"][idx])), 3),
            "ndbi_after": round(float(np.nanmean(a["NDBI"][idx])), 3),
            "candidate_parcels": touches,
            "status": "INFERRED from Sentinel-2 change detection; confirm visually",
        }
        feats.append({"type": "Feature", "properties": props, "geometry": mapping(reproject(g_utm, to_wgs))})
    feats.sort(key=lambda f: -f["properties"]["area_ha"])
    return feats, keep, geoms_utm


def previews(b: dict, a: dict, changed: np.ndarray, keep: np.ndarray, tr, parcels_utm, search_utm, inside: np.ndarray,
             feats: list, geoms_utm: dict, out: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    H, W = b["NDVI"].shape
    extent = [tr.c, tr.c + tr.a * W, tr.f + tr.e * H, tr.f]

    # one stretch for both dates (2-98 % per band over clear pixels in the search area) so before/after are comparable
    lims = {}
    for band in ("B4", "B3", "B2"):
        vals = np.concatenate([b[band][inside], a[band][inside]])
        vals = vals[np.isfinite(vals)]
        lims[band] = np.percentile(vals, [2, 98]) if vals.size else (0.0, 0.3)

    def rgb(d):
        chans = []
        for band in ("B4", "B3", "B2"):
            lo, hi = lims[band]
            chans.append(np.clip((d[band] - lo) / max(hi - lo, 1e-6), 0, 1))
        return np.nan_to_num(np.dstack(chans) ** 0.8, nan=0.0)

    def outline(ax, geom, **kw):
        for part in (geom.geoms if hasattr(geom, "geoms") else [geom]):
            x, y = part.exterior.xy
            ax.plot(x, y, **kw)

    def decorate(ax, label_fids=True):
        for _, pg in parcels_utm:
            outline(ax, pg, color="yellow", lw=0.9)
        outline(ax, search_utm, color="white", lw=0.6, ls="--")
        for f in feats:
            fid = f["properties"]["fid"]
            outline(ax, geoms_utm[fid], color="magenta", lw=1.4)
            if label_fids:
                c = geoms_utm[fid].centroid
                ax.annotate(f"fid {fid}\n{f['properties']['area_ha']:.0f} ha", (c.x, c.y), color="magenta", fontsize=8,
                            ha="center", va="center", bbox=dict(fc="black", alpha=0.5, lw=0))
        ax.set_xlabel("UTM 24S E (m)")
        ax.set_ylabel("N (m)")

    # overview frames
    for title, fname, d, overlay in (
        ("Before composite (RGB); yellow = candidate parcels, magenta = detected change", "before_rgb.png", b, None),
        ("After composite (RGB)", "after_rgb.png", a, None),
        ("After composite; red = change pixels, magenta = kept blobs", "change_overlay.png", a, changed),
    ):
        fig, ax = plt.subplots(figsize=(7, 7 * H / W))
        ax.imshow(rgb(d), extent=extent)
        if overlay is not None:
            ax.imshow(np.where(overlay, 1.0, np.nan), extent=extent, cmap="Reds", alpha=0.55, vmin=0, vmax=1)
        decorate(ax)
        ax.set_title(title, fontsize=9)
        fig.savefig(out / fname, dpi=160, bbox_inches="tight")
        plt.close(fig)

    # zoomed before/after detail per detection
    for f in feats:
        fid = f["properties"]["fid"]
        g = geoms_utm[fid]
        x0, y0, x1, y1 = g.bounds
        m = max(300.0, 0.3 * max(x1 - x0, y1 - y0))
        fig, axes = plt.subplots(1, 2, figsize=(11, 5.5))
        for ax, d, lab in zip(axes, (b, a), ("before", "after")):
            ax.imshow(rgb(d), extent=extent)
            outline(ax, g, color="magenta", lw=1.4)
            for _, pg in parcels_utm:
                outline(ax, pg, color="yellow", lw=0.8)
            ax.set_xlim(x0 - m, x1 + m)
            ax.set_ylim(y0 - m, y1 + m)
            ax.set_title(f"fid {fid}: {lab}", fontsize=10)
            ax.set_xlabel("E (m)")
        p = f["properties"]
        fig.suptitle(f"fid {fid}  {p['area_ha']:.1f} ha  ({p['lat']:.4f}, {p['lon']:.4f})  NDVI {p['ndvi_before']:.2f} -> {p['ndvi_after']:.2f}",
                     fontsize=10)
        fig.savefig(out / f"detail_fid{fid}.png", dpi=160, bbox_inches="tight")
        plt.close(fig)


def plot_timeseries(csv_path: Path, out: Path, construction_start: str = "2026-01"):
    """Line chart of monthly NDVI per detected polygon from ndvi_timeseries.csv."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    series: dict[str, list] = {}
    with csv_path.open() as fh:
        for row in csv.DictReader(fh):
            if row["ndvi_mean"] != "":
                series.setdefault(row["fid"], []).append((row["month"], float(row["ndvi_mean"]), int(row["n_scenes"])))
    if not series:
        return
    fig, ax = plt.subplots(figsize=(9, 4))
    months = sorted({m for v in series.values() for m, _, _ in v})
    xi = {m: i for i, m in enumerate(months)}
    for fid, rows in sorted(series.items(), key=lambda kv: int(kv[0])):
        rows.sort()
        ax.plot([xi[m] for m, _, _ in rows], [v for _, v, _ in rows], marker="o", ms=3, label=f"fid {fid}")
    if construction_start in xi:
        ax.axvline(xi[construction_start] - 0.5, color="grey", ls="--", lw=1)
        ax.text(xi[construction_start] - 0.4, ax.get_ylim()[1] * 0.97, "reported construction start", fontsize=8, va="top", color="grey")
    ax.set_xticks(range(len(months)))
    ax.set_xticklabels(months, rotation=90, fontsize=7)
    ax.set_ylabel("mean NDVI (monthly median composite)")
    ax.set_ylim(0, max(0.8, ax.get_ylim()[1]))
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "ndvi_timeseries.png", dpi=160)
    plt.close(fig)


# ------------------------------------------------------------------ time series (EE, light)
def month_starts(start: date, end: date):
    d = date(start.year, start.month, 1)
    while d < end:
        nxt = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
        yield d, min(nxt, end)
        d = nxt


def timeseries(ee, feats: list, region, args, out: Path):
    fc = ee.FeatureCollection([ee.Feature(ee.Geometry(f["geometry"]), {"fid": f["properties"]["fid"]}) for f in feats])
    rows = []
    for m0, m1 in month_starts(date.fromisoformat(args.ts_start), date.fromisoformat(args.after[1])):
        col = (
            ee.ImageCollection(S2).filterBounds(region).filterDate(str(m0), str(m1))
            .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", args.cloud_pct))
        )
        n = col.size().getInfo()
        if n == 0:
            rows.extend({"month": m0.strftime("%Y-%m"), "fid": f["properties"]["fid"], "ndvi_mean": "", "n_scenes": 0} for f in feats)
            print(f"  {m0:%Y-%m}: no scenes", file=sys.stderr)
            continue
        img = col.map(lambda i: mask_and_index(ee, i)).median().select("NDVI")
        stats = img.reduceRegions(fc, ee.Reducer.mean(), args.scale, tileScale=args.tile_scale).getInfo()
        for f in stats["features"]:
            p = f["properties"]
            v = p.get("mean")
            rows.append({"month": m0.strftime("%Y-%m"), "fid": p["fid"], "ndvi_mean": round(v, 4) if isinstance(v, (int, float)) else "", "n_scenes": n})
        print(f"  {m0:%Y-%m}: {n} scenes", file=sys.stderr)
    with (out / "ndvi_timeseries.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["month", "fid", "ndvi_mean", "n_scenes"])
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------ cli
def parse_args() -> argparse.Namespace:
    today = date.today()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--search", type=Path, default=DEFAULT_SEARCH, help="GeoJSON of candidate parcels / search polygons")
    p.add_argument("--buffer-m", type=float, default=500, help="buffer around search polygons (m)")
    p.add_argument("--before", nargs=2, metavar=("START", "END"), default=["2025-07-01", "2026-01-01"])
    p.add_argument("--after", nargs=2, metavar=("START", "END"), default=[str(today - timedelta(days=95)), str(today + timedelta(days=1))])
    p.add_argument("--cloud-pct", type=int, default=60, help="scene-level CLOUDY_PIXEL_PERCENTAGE cutoff")
    p.add_argument("--max-scenes", type=int, default=40, help="clearest N scenes per window go into the median")
    p.add_argument("--scale", type=float, default=10, help="output pixel size (m); 20 halves the download size")
    p.add_argument("--min-ha", type=float, default=5.0, help="smallest change blob to keep")
    p.add_argument("--ndvi-after-max", type=float, default=0.25)
    p.add_argument("--ndvi-drop", type=float, default=0.15)
    p.add_argument("--ndbi-rise", type=float, default=0.10)
    p.add_argument("--bsi-rise", type=float, default=0.10)
    p.add_argument("--reuse", action="store_true", help="skip Earth Engine and reuse the GeoTIFFs already in --out-dir")
    p.add_argument("--timeseries", action="store_true", help="monthly NDVI per detected polygon since --ts-start (uses EE)")
    p.add_argument("--ts-start", default="2025-01-01")
    p.add_argument("--tile-scale", type=int, default=4, help="EE tileScale for the time-series reduce (1-16)")
    p.add_argument("--no-thumbs", action="store_true", help="skip the PNG previews")
    p.add_argument("--ee-project", default=DEFAULT_EE_PROJECT)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    a = p.parse_args()
    for pair in (a.before, a.after):
        if date.fromisoformat(pair[0]) >= date.fromisoformat(pair[1]):
            p.error(f"window start must precede end: {pair}")
    if date.fromisoformat(a.before[1]) > date.fromisoformat(a.after[0]):
        p.error("BEFORE window must end on or before AFTER window starts")
    if a.scale <= 0 or a.min_ha <= 0 or a.max_scenes <= 0:
        p.error("--scale, --min-ha and --max-scenes must be positive")
    if not a.search.exists():
        p.error(f"search file not found: {a.search}")
    return a


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    tif_b, tif_a = args.out_dir / "before_s2.tif", args.out_dir / "after_s2.tif"

    # search geometry, built locally in UTM so buffers are in metres
    from shapely.ops import unary_union

    parcels = load_parcels(args.search)
    fwd = wgs_to_utm()
    parcels_utm = [(pid, reproject(g, fwd)) for pid, g in parcels]
    search_utm = unary_union([g.buffer(args.buffer_m) for _, g in parcels_utm])
    minx, miny, maxx, maxy = reproject(search_utm, utm_to_wgs()).bounds
    est_bytes = (search_utm.bounds[2] - search_utm.bounds[0]) * (search_utm.bounds[3] - search_utm.bounds[1]) / args.scale**2 * len(OUT_BANDS) * 4
    print(f"search area: {search_utm.area / 1e4:.0f} ha from {args.search.name} (+{args.buffer_m:.0f} m); "
          f"download ~{est_bytes / 1e6:.0f} MB per composite at {args.scale:g} m", file=sys.stderr)
    if est_bytes > MAX_DOWNLOAD_BYTES:
        raise SystemExit("Search box too large for a direct download; use --scale 20 (or a smaller --search / --buffer-m)")

    ee = None
    n_b = n_a = used_b = used_a = None
    if args.reuse and tif_b.exists() and tif_a.exists():
        print("reusing existing GeoTIFFs (no Earth Engine calls)", file=sys.stderr)
    else:
        if args.reuse:
            print("--reuse given but GeoTIFFs not found; fetching from Earth Engine", file=sys.stderr)
        ee = init_ee(args.ee_project)
        region = ee.Geometry.Rectangle([minx, miny, maxx, maxy])
        img_b, n_b, used_b = composite(ee, region, *args.before, args.cloud_pct, args.max_scenes)
        img_a, n_a, used_a = composite(ee, region, *args.after, args.cloud_pct, args.max_scenes)
        print(f"scenes: before {n_b} available / {used_b} used ({args.before[0]}..{args.before[1]}); "
              f"after {n_a} / {used_a} ({args.after[0]}..{args.after[1]})", file=sys.stderr)
        for img, path in ((img_b, tif_b), (img_a, tif_a)):
            nbytes = download_tif(ee, img, region, args.scale, path)
            print(f"  wrote {path.name} ({nbytes / 1e6:.1f} MB)", file=sys.stderr)

    b, tr = read_tif(tif_b)
    a, tr_a = read_tif(tif_a)
    if b["NDVI"].shape != a["NDVI"].shape or tr != tr_a:
        raise SystemExit("before/after GeoTIFFs are on different grids; delete them and rerun without --reuse")
    inside = rasterize([search_utm], b["NDVI"].shape, tr)
    valid_frac = float(np.mean(np.isfinite(a["NDVI"][inside]))) if inside.any() else 0.0
    if valid_frac < 0.6:
        print(f"warning: only {valid_frac:.0%} of the search area has clear pixels in the AFTER composite; widen --after", file=sys.stderr)

    changed = change_mask(b, a, inside, args)
    feats, keep, geoms_utm = vectorize(changed, b, a, tr, args.scale, parcels_utm, args)
    found = {"type": "FeatureCollection", "features": feats}
    (args.out_dir / "detected_footprints.geojson").write_text(json.dumps(found, indent=1))

    print(f"\n{len(feats)} change polygon(s) >= {args.min_ha} ha "
          f"(all change pixels: {changed.sum() * args.scale**2 / 1e4:.0f} ha):")
    for f in feats:
        p = f["properties"]
        print(f"  fid {p['fid']:>3}  {p['area_ha']:7.1f} ha  lat {p['lat']:.4f} lon {p['lon']:.4f}  "
              f"NDVI {p['ndvi_before']:.2f} -> {p['ndvi_after']:.2f}  NDBI {p['ndbi_before']:.2f} -> {p['ndbi_after']:.2f}  "
              f"parcels {p['candidate_parcels'] or '-'}")
    if not feats:
        print("  none. Try --reuse with a lower --min-ha / --ndvi-drop, a later --after window, or check the PNGs for cloud gaps.")

    summary = {
        "search_file": str(args.search), "buffer_m": args.buffer_m, "scale_m": args.scale,
        "before": args.before, "after": args.after,
        "scenes_before_available": n_b, "scenes_before_used": used_b, "scenes_after_available": n_a, "scenes_after_used": used_a,
        "after_clear_fraction": round(valid_frac, 3), "cloud_pct": args.cloud_pct,
        "thresholds": {k: getattr(args, k) for k in ("min_ha", "ndvi_after_max", "ndvi_drop", "ndbi_rise", "bsi_rise")},
        "n_detected": len(feats), "total_detected_ha": round(sum(f["properties"]["area_ha"] for f in feats), 1),
        "status": "INFERRED from Sentinel-2 change detection; confirm visually",
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    if not args.no_thumbs:
        previews(b, a, changed, keep, tr, parcels_utm, search_utm, inside, feats, geoms_utm, args.out_dir)
    ts_csv = args.out_dir / "ndvi_timeseries.csv"
    if args.timeseries and feats:
        ee = ee or init_ee(args.ee_project)
        region = ee.Geometry.Rectangle([minx, miny, maxx, maxy])
        print("\nmonthly NDVI per polygon:", file=sys.stderr)
        timeseries(ee, feats, region, args, args.out_dir)
    if ts_csv.exists() and not args.no_thumbs:
        plot_timeseries(ts_csv, args.out_dir)
    print(f"\nwrote {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
