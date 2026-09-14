# Sovereign Compute Nexus Explorer

An interactive map of the Brazil data-centre siting model. Two views:

- **Ceará case study** — all 2,808 H3 resolution-8 cells in the 50 km × 50 km box. Switch the
  fill between the four model phases and the underlying raw fields (NDVI, NDWI, Sentinel-1 VV/VH,
  land cover, distance to grid and fibre, nearby renewables). Click any cell for its full record.
  The four threshold sliders re-test every cell and re-sort the Pareto frontier live.
- **Brazil overview** — all 27 states, colourable by suitability score, curtailed energy,
  transmission headroom, renewable share, installed capacity, facility count, protected land and
  indigenous land, with the ONS transmission network, substations and 336 data-centre points as
  overlays.

Everything runs client-side. There is no tile server, no API and no build step — open
`index.html` and it works, online or off.

## Files

| File | What it is |
| --- | --- |
| `index.html` | The page: markup and all CSS |
| `app.js` | Projection, rendering, the live constraint solver and the Pareto sort |
| `data.js` | The dataset as one `window.SCN` object (~1.1 MB) |
| `build_data.py` | Regenerates `data.js` from the workspace outputs |
| `data/` | Simplified boundary geometry extracted from `brazil_territorial_layers.gpkg` |

## Run it locally

```bash
python3 -m http.server 8000     # then open http://localhost:8000
```

Opening `index.html` directly from the filesystem also works.

## Publish it on GitHub Pages

```bash
cd map_explorer
git init -b main
git add .
git commit -m "Sovereign Compute Nexus map explorer"
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main
```

Then in the repository: **Settings → Pages → Source: Deploy from a branch → `main` / `root`**.
The site appears at `https://<you>.github.io/<repo>/` within a minute or two. The `.nojekyll`
file is there so Pages serves the directory as-is.

## Regenerating the data

`build_data.py` reads the current phase outputs and rewrites `data.js`:

```bash
python3 build_data.py
```

It pulls from `clean_data/sovereign_compute_nexus/phase4_optimization/phase4_all_h3_scored.geojson`,
the ONS grid and curtailment CSVs, the merged data-centre point layer, the state suitability table,
and the boundary geometry in `data/`. Re-run it after any pipeline change and the map updates.

## A defect the explorer exposes

The left rail carries a checkbox labelled **Fix the 0 km bug**, off by default so the map
reproduces the published run exactly (819 feasible, 448 on the frontier, 25 recommended).

`run_scn_phase4_optimization.py` reads each distance as `float(row.get(field, np.inf) or np.inf)`.
In Python `0.0` is falsy, so a distance of **exactly 0 km reads as infinite**. The result is that
**147 cells sitting directly on a transmission line were excluded for being too far from one**.

Turning the fix on raises the feasible set from 819 to 966 and the frontier from 448 to 505, and
promotes a cell with a resilience score of **74.33** — higher than the current top-ranked
recommendation at 73.52. The same coercion applies to `nearest_hv_ons_bus_km`,
`nearest_idc_km` and `p_deg_rs`.

The fix is to test for `None` rather than falsiness, e.g.:

```python
v = row.get("nearest_ons_line_km")
v = np.inf if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)
```

## Sources

ONS Dados Abertos (grid topology, generation, constrained-off records) · geobr / IPEA (states,
municipalities, conservation units, indigenous lands) · PeeringDB and OpenStreetMap (data-centre
facilities) · MapBiomas-family land cover and surface water · Copernicus Sentinel-1 and Sentinel-2
via Google Earth Engine.

Generated 14 September 2026 from `~/Projects/Brazil`.
