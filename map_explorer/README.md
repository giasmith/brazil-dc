# Sovereign Compute Nexus Explorer

An interactive map of the Brazil data-center siting model. Two views:

- **Ceará case study** — all 2,808 H3 resolution-8 cells in the 50 km × 50 km box. Switch the
  fill between the four model phases and the underlying raw fields (NDVI, NDWI, Sentinel-1 VV/VH,
  land cover, distance to grid and fiber, nearby renewables). Click any cell for its full record.
  The four threshold sliders re-test every cell and re-sort the Pareto frontier live.
- **Brazil overview** — all 27 states, colorable by suitability score, curtailed energy,
  transmission headroom, renewable share, installed capacity, facility count, protected land and
  indigenous land, with the ONS transmission network, substations and 336 data-center points as
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
| `data/brazilian_data_centers.csv` | Curated facility list (operator, status, MW, cooling notes) behind the diamond markers |
| `data/cases.json` | Case files: an observed construction footprint, an observation summary and a legal record, each attached to one row of the facility list by `dc_name`. First entry: the Pecém (Omnia / TikTok) site, located in `../analysis/pecem_dc_location/` and contested in ACP 0080626-66.2026.4.05.8100 |

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
the ONS grid and curtailment CSVs, the merged data-center point layer, the state suitability table,
and the boundary geometry in `data/`. Re-run it after any pipeline change and the map updates.

## A defect the explorer exposed (fixed September 17, 2026)

Until September 2026, `run_scn_phase4_optimization.py` read each distance as
`float(row.get(field, np.inf) or np.inf)`. In Python `0.0` is falsy, so a distance of
**exactly 0 km read as infinite** and **147 cells sitting directly on a transmission line were
excluded for being too far from one**. The same coercion applied to `nearest_hv_ons_bus_km`,
`nearest_idc_km` and `p_deg_rs`.

The script now uses a None/NaN-safe `as_float` helper and the Phase 4 outputs have been
regenerated: 966 feasible, 505 on the frontier, 25 recommended (12 flagged for review), top
score 74.33. The explorer opens on that corrected run. The left rail carries a checkbox
labeled **Reproduce the pre-fix run** that re-applies the old coercion live, so the May 2026
result (819 / 448 / 25, top score 73.52) remains one click away.

The fix is to test for `None` rather than falsiness, e.g.:

```python
v = row.get("nearest_ons_line_km")
v = np.inf if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)
```

## Sources

ONS Dados Abertos (grid topology, generation, constrained-off records) · geobr / IPEA (states,
municipalities, conservation units, indigenous lands) · PeeringDB and OpenStreetMap (data-center
facilities) · MapBiomas-family land cover and surface water · Copernicus Sentinel-1 and Sentinel-2
via Google Earth Engine.

Generated September 14, 2026 from `~/Projects/Brazil`.

## Case files

`data/cases.json` is hand-curated. Each record needs `dc_name` (must match exactly one `Operator & Campus`
in the facility CSV), `site.polygon` (a closed lon/lat ring), `observation`, `project`, `legal` and `sources`.
`build_data.py` links it to the facility row; in the Ceará view the footprint draws as a dashed outline
under the facility diamond, and the facility record gains a "Case file" section. Claims are the
plaintiffs' allegations and are labelled as such; locations derived from imagery carry the
`inferred_location` flag.

## Record dock, trade-off types and policy explanations (2026-10-05)

- The selected cell / facility / state record opens in a dock under the map (`#dock`), not in the legend rail.
- `Frontier trade-off type` is a descriptive layer: `build_data.py` groups the default-threshold frontier cells
  with k-means (k = 4, 12 fixed seeds, stdlib) on the objectives that vary (curtailment is constant in
  single-state runs), names each group from where its mean profile sits (bottom third = strength, top third =
  weakness; traits shared by three or more groups are dropped), and stores `tt`, `ttMeta`, `ttInfo`, `oExt`.
  It changes no gate or score.
- Every cell record shows an objective profile (bars across the frontier's range) and a "why Low / Medium /
  Critical" block built from the Phase 3 trigger tokens, the distance to the nearest legal constraint, the
  evidence tags and the thresholds in `phase3_policy_summary.json`. Conservation-unit names that the phase-2
  join left as "Unnamed feature" are resolved from `data/protected_ceara.json`.
