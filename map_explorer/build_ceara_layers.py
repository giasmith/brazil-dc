#!/usr/bin/env python3
"""Rebuild the Ceara municipality payload the explorer labels the case-study map with.

Reads the municipalities layer straight out of brazil_territorial_layers.gpkg with sqlite3 and a
small WKB reader, so this runs without geopandas. Re-run it whenever the study box changes:
the cell counts and the label points are both box-dependent.

    python3 map_explorer/build_ceara_layers.py [--min-cells 12]
"""
import argparse, csv, json, math, sqlite3, struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GPKG = ROOT / "clean_data" / "brazil_territorial_layers.gpkg"
BASELINE = ROOT / "clean_data" / "sovereign_compute_nexus" / "phase1_h3_baseline.csv"
BOX = ROOT / "clean_data" / "sovereign_compute_nexus" / "ceara_case_study_box.geojson"
OUT = Path(__file__).resolve().parent / "data" / "ceara_munis.json"


def read_gpkg_geometry(blob: bytes):
    """GeoPackage BLOB -> list of rings [[ (lon, lat), ... ], ...]. Polygon and MultiPolygon only."""
    if blob[:2] != b"GP":
        raise ValueError("not a GeoPackage geometry blob")
    flags = blob[3]
    env = (flags >> 1) & 0x07
    env_bytes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]
    return read_wkb(blob[8 + env_bytes:])


def read_wkb(buf: bytes):
    """Polygon / MultiPolygon -> list of rings. Each geometry carries its own byte-order byte,
    including every member of a MultiPolygon, so endianness is re-read per geometry rather than
    inferred from the preceding byte."""
    rings = []
    pos = 0

    def u32(endian):
        nonlocal pos
        v = struct.unpack_from(endian + "I", buf, pos)[0]
        pos += 4
        return v

    def geometry():
        nonlocal pos
        endian = "<" if buf[pos] == 1 else ">"
        pos += 1
        gtype = u32(endian) % 1000
        if gtype == 3:
            for _ in range(u32(endian)):
                n_pts = u32(endian)
                pts = struct.unpack_from(endian + f"{2 * n_pts}d", buf, pos)
                pos += 16 * n_pts
                rings.append([(pts[i], pts[i + 1]) for i in range(0, len(pts), 2)])
        elif gtype == 6:
            for _ in range(u32(endian)):
                geometry()
        else:
            raise ValueError(f"unsupported geometry type {gtype}")

    geometry()
    return rings


def point_in_rings(lon, lat, rings):
    """True when the point is inside the outer ring an odd number of times across all rings
    (holes therefore subtract, which is what we want for an island-and-lagoon coastline)."""
    inside = False
    for ring in rings:
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % n]
            if (y1 > lat) != (y2 > lat):
                xi = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
                if lon < xi:
                    inside = not inside
    return inside


def _dp(ring, tol):
    """Douglas-Peucker on an open polyline, iterative so a long coastline cannot blow the stack."""
    if len(ring) < 3:
        return list(ring)
    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        x1, y1 = ring[a]
        x2, y2 = ring[b]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy)
        worst, worst_i = -1.0, -1
        for i in range(a + 1, b):
            px, py = ring[i]
            d = (abs(dy * px - dx * py + x2 * y1 - y2 * x1) / norm) if norm else math.hypot(px - x1, py - y1)
            if d > worst:
                worst, worst_i = d, i
        if worst > tol:
            keep[worst_i] = True
            stack.append((a, worst_i))
            stack.append((worst_i, b))
    return [p for p, k in zip(ring, keep) if k]


def simplify(ring, tol):
    """Simplify a closed ring.

    Running Douglas-Peucker straight across a closed ring collapses it: the first and last vertex
    coincide, so the anchor segment has zero length and every interior point measures zero distance
    from it. Split the ring at the vertex farthest from its start and simplify the two halves.
    """
    if len(ring) < 4:
        return list(ring)
    closed = ring[0] == ring[-1]
    pts = ring[:-1] if closed else list(ring)
    if len(pts) < 4:
        return list(ring)
    x0, y0 = pts[0]
    k = max(range(1, len(pts)), key=lambda i: (pts[i][0] - x0) ** 2 + (pts[i][1] - y0) ** 2)
    out = _dp(pts[:k + 1], tol)[:-1] + _dp(pts[k:] + [pts[0]], tol)[:-1]
    if len(out) < 3:
        return list(ring)
    return out + [out[0]]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-cells", type=int, default=12,
                    help="Only label a municipality holding at least this many cells in the box.")
    ap.add_argument("--tolerance", type=float, default=0.0015, help="Simplification tolerance in degrees.")
    args = ap.parse_args()

    box = json.load(open(BOX))["features"][0]["geometry"]["coordinates"][0]
    W = min(p[0] for p in box); E = max(p[0] for p in box)
    S = min(p[1] for p in box); N = max(p[1] for p in box)

    cells = [r for r in csv.DictReader(open(BASELINE)) if r["state_code"] == "CE"]
    print(f"box lon {W:.4f}..{E:.4f} lat {S:.4f}..{N:.4f}; {len(cells)} Ceara cells")

    con = sqlite3.connect(GPKG)
    con.text_factory = str
    cols = [r[1] for r in con.execute("PRAGMA table_info(all_layers)")]
    namecol = next(c for c in ("name_muni", "nm_mun", "name", "nome") if c in cols)
    geomcol = next(c for c in ("geom", "geometry") if c in cols)
    rows = con.execute(
        f"SELECT {namecol}, {geomcol} FROM all_layers WHERE source_layer='municipalities'"
    ).fetchall()
    print(f"{len(rows)} municipalities in the layer; column '{namecol}'")

    munis = []
    for name, blob in rows:
        if not name or blob is None:
            continue
        try:
            rings = read_gpkg_geometry(blob)
        except ValueError:
            continue
        xs = [p[0] for r in rings for p in r]
        ys = [p[1] for r in rings for p in r]
        if not xs or max(xs) < W or min(xs) > E or max(ys) < S or min(ys) > N:
            continue                                  # bounding boxes cannot overlap the study box
        hits = [(float(c["center_lon"]), float(c["center_lat"]))
                for c in cells if point_in_rings(float(c["center_lon"]), float(c["center_lat"]), rings)]
        if len(hits) < args.min_cells:
            continue
        mx = sum(p[0] for p in hits) / len(hits)
        my = sum(p[1] for p in hits) / len(hits)
        # label at the cell nearest the mean, so the anchor is always a real cell inside both
        # the municipality and the box, even where the municipality is concave
        lx, ly = min(hits, key=lambda p: (p[0] - mx) ** 2 + (p[1] - my) ** 2)
        simple = [simplify(r, args.tolerance) for r in rings]
        simple = [[[round(x, 5), round(y, 5)] for x, y in r] for r in simple if len(r) >= 4]
        munis.append({"nm": name, "n": len(hits), "lx": round(lx, 5), "ly": round(ly, 5), "p": simple})

    munis.sort(key=lambda m: -m["n"])
    assigned = sum(m["n"] for m in munis)
    print(f"{len(munis)} municipalities labelled, covering {assigned} of {len(cells)} Ceara cells")
    for m in munis:
        print(f"   {m['nm']:34s} {m['n']:5d} cells   label ({m['lx']}, {m['ly']})")
    OUT.write_text(json.dumps(munis, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
