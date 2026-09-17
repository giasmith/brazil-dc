# Brazil Sovereign Compute Nexus Pipeline

Last updated: 2026-09-17

This repository is now a geospatial energy, data-center, and socio-ecological siting pipeline for Brazil. It started as a cleaned Global Energy Monitor style energy dataset, but the recent work expanded it into a reproducible research stack for the **Sovereign Compute Nexus** framework: a policy-aware digital twin that tests where hyperscale data-center infrastructure can be sited without violating power-grid, water, land-cover, protected-land, or indigenous-territory constraints.

The key idea is simple: every physical, environmental, and policy layer is harmonized into spatial decision units, then hard constraints are applied before optimization. Cheap power is not allowed to override protected land, indigenous land, water risk, or high degradation risk.

## What is in this repository

| Path | Contents |
| --- | --- |
| `scripts/` | The 18 pipeline stage scripts, runnable in the order under **Reproducibility** below. |
| `gee/` | The Earth Engine Code Editor script for the Sentinel-1/2 exports. |
| `docs/` | Phase 1 data manifest, SCN methodology note, the Phase 3 ReData policy corpus (Markdown + JSON), and the advisor progress memo. |
| `map_explorer/` | A self-contained interactive map of every layer and all four phases. Open `map_explorer/index.html`, or serve the folder on GitHub Pages. |
| `phase2_rs_features_for_solver.csv` | The Sentinel-derived H3 feature table produced in Colab and consumed by Phase 2. |

Not tracked here, because it is large and fully reproducible: `clean_data/` and `data/`
(rebuild with the scripts), `papers/` and `gov_docs/` (third-party PDFs, all cited in the
policy corpus), and `preprocessing.ipynb` (~92 MB of embedded output). See `.gitignore`.

### Known defect in Phase 4

`run_scn_phase4_optimization.py` reads each distance as
`float(row.get(field, np.inf) or np.inf)`. `0.0` is falsy in Python, so a distance of
**exactly 0 km is read as infinite** and 147 cells sitting directly on a transmission line
were excluded for being too far from one. Correcting it raises the feasible set from 819 to
966 and the Pareto frontier from 448 to 505, and promotes a cell scoring 74.33 — above the
current top-ranked recommendation at 73.52. The same coercion affects
`nearest_hv_ons_bus_km`, `nearest_idc_km` and `p_deg_rs`. The map explorer carries a toggle
that switches between the published and corrected results.

## Current Status

The repository now contains:

| Layer | Status | Main outputs |
| --- | --- | --- |
| Clean energy asset data | Complete | `clean_data/energy/` |
| Brazil territorial layers | Complete | `clean_data/brazil_territorial_layers.gpkg` |
| GEM-style synthetic physics grid | Complete | `clean_data/power_grid/` |
| Official ONS topology and generation | Complete | `clean_data/ons_official_grid/` |
| ONS vs GEM comparison | Complete | `clean_data/ons_comparison/` |
| ONS renewable curtailment history | Complete | `clean_data/ons_curtailment_history/` |
| ONS plant/weather detail curtailment | Complete | `clean_data/ons_curtailment_detail/` |
| GEM proposed generation stress overlay | Complete | `clean_data/gem_proposed_overlay/` |
| OSM data centers | Complete | `clean_data/osm_data_centers/` |
| PeeringDB plus OSM IDC point layer | Complete | `clean_data/idc_data_centers/` |
| Integrated Brazil DC investment overlay | Complete | `clean_data/integrated_dc_investment/` |
| LULC and water loading | Complete as candidate layer | `clean_data/lulc_water/` |
| Sentinel-1/2 GEE export request | Submitted to Earth Engine | `data/gee/sentinel_gee_export_manifest.json` |
| SCN Phase 1 H3 baseline | Complete | `clean_data/sovereign_compute_nexus/phase1_h3_baseline.*` |
| SCN MVP Pareto frontier | Complete | `clean_data/sovereign_compute_nexus/mvp_pareto/` |
| SCN Phase 2 RS extraction | Complete with Sentinel-derived H3 features | `clean_data/sovereign_compute_nexus/phase2_rs/` |
| SCN Phase 3 policy classifier | Complete as bounded classifier | `clean_data/sovereign_compute_nexus/phase3_policy/` |
| SCN Phase 4 constrained optimization | Complete with real Phase 2/3 inputs | `clean_data/sovereign_compute_nexus/phase4_optimization/` |

## Recent Data Pulled or Assembled

### 1. Territorial Layers

Script/workflow: notebook section in `preprocessing.ipynb`

The territorial base layer was pulled from the `geobr` prepared-data releases and saved as a single GeoPackage.

| Layer | Features | Source file |
| --- | ---: | --- |
| Brazilian states | 27 | `states_2025_simplified.parquet` |
| Brazilian municipalities | 5,573 | `municipalities_2025_simplified.parquet` |
| Protected lands / conservation units | 3,001 | `conservationunits_202503_simplified.parquet` |
| Indigenous lands | 637 | `indigenouslands_2025_simplified.parquet` |

Outputs:

- `clean_data/brazil_territorial_layers.gpkg`
- `clean_data/brazil_territorial_layers_map.html`
- `clean_data/brazil_territorial_layers_metadata.json`

Finding:

This layer became the legal and spatial backbone for the rest of the pipeline. Protected lands, indigenous lands, and outside-boundary cells are treated as deterministic exclusions in the SCN case study.

### 2. Clean Energy Asset Data

Script/workflow: original notebook cleaning pipeline

The base energy workbook was cleaned into 19 standardized CSV files.

Source:

- `/Users/nqj5zk/Library/CloudStorage/OneDrive-UniversityofVirginia/brazil/Portal-Energetico-Tracker-2026-03-24.xlsx`

Summary:

| Metric | Value |
| --- | ---: |
| Workbook sheets | 34 |
| Data sheets processed | 19 |
| Rows processed | 22,323 |
| Empty columns removed | 49 |
| Rows removed | 0 |

Outputs:

- `clean_data/energy/solar.csv`
- `clean_data/energy/wind.csv`
- `clean_data/energy/hydropower.csv`
- `clean_data/energy/bioenergy.csv`
- `clean_data/energy/oil_and_gas_plants.csv`
- `clean_data/energy/cleaning_report.json`

Finding:

The clean energy asset tables preserve all original rows and provide the project-level generation inventory used to build the first GEM-style grid topology.

### 3. Physics-Based GEM-Style Grid

Script:

```bash
python3 scripts/build_brazil_power_grid.py
```

This script converted the cleaned energy asset tables into a physics-inspired DC power-flow topology. Because the original GEM-style data did not include full transmission vectors, corridors were inferred from generator geography using Delaunay and nearest-neighbor connectivity.

Outputs:

- `clean_data/power_grid/brazil_power_grid_model.gpkg`
- `clean_data/power_grid/brazil_power_grid_buses.csv`
- `clean_data/power_grid/brazil_power_grid_branches.csv`
- `clean_data/power_grid/brazil_power_grid_generators.csv`
- `clean_data/power_grid/brazil_power_grid_interactive_map.html`

Findings:

| Metric | Value |
| --- | ---: |
| Buses | 232 |
| Branches | 826 |
| Generators | 6,238 |
| Installed capacity | 229,697.9 MW |
| Modeled available generation | 120,047.6 MW |
| Overloaded branches | 0 |

Interpretation:

This was useful as an initial topology, but it is not official transmission data. It is best used as a GEM-derived reference or comparison layer.

### 4. Official ONS Grid and Generation

Script:

```bash
python3 scripts/build_ons_official_grid.py
```

The official ONS topology and generation capacity data were pulled and organized into a DC power-flow model. ONS connection-point labels were matched to substations where possible, with state/subsystem weighted fallback assignment for unmatched generation units.

Outputs:

- `clean_data/ons_official_grid/ons_official_grid_model.gpkg`
- `clean_data/ons_official_grid/ons_official_grid_buses.csv`
- `clean_data/ons_official_grid/ons_official_grid_branches.csv`
- `clean_data/ons_official_grid/ons_clean_generation_assignments.csv`
- `clean_data/ons_official_grid/ons_official_grid_interactive_map.html`
- `clean_data/ons_official_grid/energy_mix_ons_vs_gem.csv`

Findings:

| Metric | Value |
| --- | ---: |
| ONS buses | 1,705 |
| ONS active branches | 1,838 |
| ONS generation units | 4,626 |
| Installed capacity | 198,138.3 MW |
| Modeled generation | 79,955.9 MW |
| Modeled load | 79,608.9 MW |
| Base overloaded branches | 1 |
| Max branch utilization | 122.5% |

Energy mix comparison:

| Technology | ONS installed MW | ONS share | GEM installed MW | GEM share |
| --- | ---: | ---: | ---: | ---: |
| Hydropower | 109,678.4 | 55.4% | 109,667.0 | 47.7% |
| Wind | 33,964.7 | 17.1% | 37,545.6 | 16.3% |
| Solar | 20,622.3 | 10.4% | 29,679.8 | 12.9% |
| Oil/gas | 18,908.3 | 9.5% | 28,567.1 | 12.4% |
| Bioenergy | 4,235.4 | 2.1% | 17,846.4 | 7.8% |
| Coal | 2,900.4 | 1.5% | 2,997.0 | 1.3% |
| Nuclear | 1,990.0 | 1.0% | 3,395.0 | 1.5% |

Interpretation:

ONS is the better source for official system topology and generation capacity. GEM is valuable for cross-checking project-level assets and future/proposed assets, but it is not identical to ONS in coverage or classification.

### 5. ONS Curtailment History

Script:

```bash
python3 scripts/analyze_ons_curtailment_history.py
```

This stage pulled and cleaned ONS constrained-off aggregate files for wind and solar.

Outputs:

- `clean_data/ons_curtailment_history/ons_curtailment_history_report.html`
- `clean_data/ons_curtailment_history/curtailment_annual.csv`
- `clean_data/ons_curtailment_history/curtailment_monthly.csv`
- `clean_data/ons_curtailment_history/curtailment_by_state_total.csv`
- `clean_data/ons_curtailment_history/curtailment_by_reason_origin.csv`

Findings:

| Metric | Value |
| --- | ---: |
| Coverage | 2021-10 to 2026-05 |
| Monthly files processed | 82 |
| Generated MWh | 502.3 TWh |
| Curtailed MWh | 63.5 TWh |
| Wind curtailed | 45.6 TWh |
| Solar curtailed | 17.9 TWh |
| Curtailment as share of potential | 11.2% |
| Transmission-text MWh | 10.7 TWh |

Interpretation:

Curtailment is large enough to matter for data-center siting. The strongest evidence of transmission constraints comes from combining ONS curtailment records with topology stress tests, not from generation capacity alone.

### 6. ONS Plant/Weather Detail Curtailment

Script:

```bash
python3 scripts/analyze_ons_curtailment_detail.py
```

This stage processed ONS detail files with half-hourly plant/conjunto estimates, verified generation, and weather-related fields.

Outputs:

- `clean_data/ons_curtailment_detail/ons_curtailment_detail_report.html`
- `clean_data/ons_curtailment_detail/detail_plant_rankings.csv`
- `clean_data/ons_curtailment_detail/detail_weather_bins.csv`
- `clean_data/ons_curtailment_detail/detail_state_totals.csv`
- `clean_data/ons_curtailment_detail/detail_monthly.csv`

Findings:

| Metric | Value |
| --- | ---: |
| Files processed | 82 |
| Detail rows processed | 91,985,149 |
| Distinct plant IDs | 1,593 |
| Estimated generation | 499.4 TWh |
| Verified generation | 498.5 TWh |
| Total estimated-minus-verified gap | 108.6 TWh |
| Wind gap | 86.2 TWh |
| Solar gap | 22.5 TWh |
| Matched restricted gap | 69.0 TWh |
| Matched transmission-text gap | 11.4 TWh |

Interpretation:

The detail data is broader than official curtailment because it captures all estimated-minus-verified shortfall. The matched restricted columns isolate rows that also appear in official constrained-off records.

### 7. GEM Proposed Generation Overlay on ONS Topology

Script:

```bash
python3 scripts/overlay_gem_proposed_on_ons.py
```

This scenario placed proposed GEM assets onto the official ONS topology while holding transmission fixed.

Outputs:

- `clean_data/gem_proposed_overlay/gem_proposed_on_ons_topology_map.html`
- `clean_data/gem_proposed_overlay/gem_proposed_overlay_model.gpkg`
- `clean_data/gem_proposed_overlay/gem_proposed_generators_assigned.csv`
- `clean_data/gem_proposed_overlay/top_stressed_branches.csv`

Findings:

| Metric | Value |
| --- | ---: |
| Proposed GEM generators | 3,552 |
| Proposed capacity | 463,004.6 MW |
| Proposed dispatch proxy | 188,639.6 MW |
| Baseline overloaded branches | 1 |
| Scenario overloaded branches | 236 |
| Baseline branches over 75% | 9 |
| Scenario branches over 75% | 335 |
| Incremental overload proxy | 320,651.7 MW |
| Max scenario utilization | 2,530.5% |

Interpretation:

If proposed generation is added to the current ONS topology without corresponding transmission expansion, the model shows major localized transmission stress. This is a stress proxy, not a full optimal-power-flow curtailment forecast.

### 8. Data Center and IDC Point Layers

OSM script:

```bash
python3 scripts/download_osm_brazil_data_centers.py
```

IDC script:

```bash
python3 scripts/download_brazil_idc_points.py
```

Two layers were built:

1. A strict OpenStreetMap layer using `telecom=data_center` or `telecom=data_center`.
2. A larger IDC point layer using PeeringDB public facility data plus unique OSM-only points.

Outputs:

- `clean_data/osm_data_centers/brazil_osm_data_centers_map.html`
- `clean_data/osm_data_centers/brazil_osm_data_centers.gpkg`
- `clean_data/idc_data_centers/brazil_idc_points_map.html`
- `clean_data/idc_data_centers/brazil_idc_points_merged.gpkg`
- `clean_data/idc_data_centers/brazil_idc_points_by_state.csv`

Findings:

| Dataset | Count |
| --- | ---: |
| Strict OSM data-center features | 57 |
| PeeringDB Brazil facility points | 302 |
| Merged PeeringDB plus OSM points | 336 |
| Minimum target met | Yes, 200+ points |

Interpretation:

The PeeringDB plus OSM layer is the stronger working dataset for IDC geography. OSM alone is more conservative and tag-pure, but too sparse for national screening.

### 9. Integrated DC Investment Overlay

Script:

```bash
python3 scripts/build_integrated_dc_investment_overlay.py
```

This overlay combined states, protected/indigenous lands, IDC points, ONS topology, GEM current/proposed generation, and curtailment-derived stress into a preliminary state-level data-center suitability score.

Outputs:

- `clean_data/integrated_dc_investment/brazil_integrated_dc_infrastructure_map.html`
- `clean_data/integrated_dc_investment/brazil_integrated_dc_layers.gpkg`
- `clean_data/integrated_dc_investment/brazil_state_dc_investor_suitability.csv`

Scoring weights:

| Component | Weight |
| --- | ---: |
| Market/interconnect score | 0.30 |
| Grid access score | 0.20 |
| Clean power score | 0.20 |
| Transmission headroom score | 0.15 |
| Environment/biome score | 0.15 |

Top preliminary states:

| Rank | State | Score | Tier |
| ---: | --- | ---: | --- |
| 1 | SP | 86.08 | Tier 1 |
| 2 | MG | 74.76 | Tier 1 |
| 3 | PR | 71.35 | Tier 1 |
| 4 | RS | 70.80 | Tier 1 |
| 5 | BA | 67.96 | Tier 2 |
| 6 | RJ | 66.04 | Tier 2 |
| 7 | CE | 64.03 | Tier 2 |
| 8 | GO | 63.66 | Tier 2 |

Interpretation:

This is a screening tool, not investment advice. Sao Paulo ranks highest because of market depth and interconnection density. Ceara remains a strong candidate because of subsea/fiber relevance and renewable position, but the model flags Northeast transmission stress and biome/climate sensitivity.

### 10. LULC and Water Layers

Script:

```bash
python3 scripts/load_lulc_water_layers.py
```

This stage loaded local LULC and water rasters from:

```text
/Users/nqj5zk/Library/CloudStorage/OneDrive-UniversityofVirginia/brazil/data
```

Sources found:

| Raster | Status | Notes |
| --- | --- | --- |
| Water surface 2024 | Complete | BigTIFF, EPSG:4326, 30 m class family |
| LULC 2024 candidate | Readable but partial filename | Source is still named `Unconfirmed 614937.crdownload` |

Outputs:

- `clean_data/lulc_water/lulc_candidate_2024.vrt`
- `clean_data/lulc_water/water_surface_2024.vrt`
- `clean_data/lulc_water/lulc_water_preview_map.html`
- `clean_data/lulc_water/lulc_class_legend.csv`
- `clean_data/lulc_water/lulc_class_area_by_state.csv`
- `clean_data/lulc_water/state_lulc_water_summary.csv`

Finding:

The LULC class codes match the MapBiomas Brazil class-code family. State summaries are approximate because they use a downsampled raster grid for speed. The full VRTs should be used for exact parcel-scale or publication-grade zonal calculations.

Important caveat:

The LULC file is readable, but still has a browser partial-download filename. Rerun the loader after the final MapBiomas file is fully downloaded and renamed.

### 11. Sentinel-1 and Sentinel-2 GEE Export Request

Python request script:

```bash
python3 scripts/request_sentinel_gee_exports.py --dry-run
```

Earth Engine Code Editor script:

```text
gee/request_sentinel_ceara_exports.js
```

Purpose:

Request the raw remote-sensing inputs needed to replace the current Phase 2 PCA proxy with a true Sentinel-based edge-effect model. The request uses the existing Ceara 50 km by 50 km case-study box:

```text
clean_data/sovereign_compute_nexus/ceara_case_study_box.geojson
```

Prepared GEE products:

| Product | Earth Engine dataset | Bands/features | Scale |
| --- | --- | --- | ---: |
| Sentinel-2 SR Harmonized | `COPERNICUS/S2_SR_HARMONIZED` | `B2`, `B3`, `B4`, `B8`, `B11`, `B12`, `NDVI`, `NDWI`, `NBR` | 10 m |
| Sentinel-1 GRD | `COPERNICUS/S1_GRD` | `VV`, `VH`, `VV_MINUS_VH`, `VV_DIV_VH` | 10 m |

Default request window:

| Parameter | Value |
| --- | --- |
| Start date | `2021-10-01` |
| End date | `2026-05-01` |
| Frequency | Quarterly composites |
| Export destination | Google Drive |
| Drive folder | `SCN_GEE_Sentinel_Ceara` |
| Local Google Drive Desktop path | `/Users/nqj5zk/Library/CloudStorage/GoogleDrive-starlab642@gmail.com/My Drive/SCN_GEE_Sentinel_Ceara` |
| Earth Engine project id | `studied-union-325415` |
| Prepared export tasks | 38 |

Outputs already created locally:

- `scripts/request_sentinel_gee_exports.py`
- `gee/request_sentinel_ceara_exports.js`
- `data/gee/sentinel_gee_export_manifest.json`

Current submission status:

The Earth Engine Python API is installed locally and the export tasks were submitted under project `studied-union-325415`.

Sentinel-2 retry note:

The first Sentinel-2 export batch failed because Earth Engine requires all exported bands to have compatible data types. The scaled Sentinel-2 reflectance bands and derived index bands produced a mixed `Float64` / `Float32` stack. The export scripts now cast output imagery with `.toFloat()`, and a corrected Sentinel-2 retry batch was submitted with `_v2` suffixes.

Submission outputs:

- `data/gee/earthengine_project.json`
- `data/gee/sentinel_gee_export_manifest.json`
- `data/gee/sentinel_gee_submitted_tasks.json`
- `data/gee/sentinel_gee_submitted_tasks_s2_v2.json`
- `data/gee/sentinel_gee_export_manifest_s2_v2.json`
- `data/gee/sentinel_gee_export_status.json`

Google Drive Desktop ingest:

```bash
python3 scripts/ingest_sentinel_drive_exports.py
```

The ingest script scans the local Google Drive Desktop folder, excludes the failed non-`_v2` Sentinel-2 batch by default, verifies each GeoTIFF with `rasterio`, and creates project-local symlinks in:

```text
data/gee/exports/
```

Current ingest status:

| Metric | Value |
| --- | ---: |
| Expected valid exports | 38 |
| Visible Drive GeoTIFFs | 1 |
| Accepted/readable GeoTIFFs | 1 |
| Missing expected exports | 37 |

The first readable file is `scn_ceara_s2_20260101_20260401_v2.tif`, with 9 float32 bands: `B2`, `B3`, `B4`, `B8`, `B11`, `B12`, `NDVI`, `NDWI`, and `NBR`.

Ingest outputs:

- `scripts/ingest_sentinel_drive_exports.py`
- `data/gee/exports/`
- `data/gee/sentinel_drive_export_catalog.csv`
- `data/gee/sentinel_drive_export_catalog.json`
- `data/gee/sentinel_drive_ingest_summary.json`

Task status can be checked with:

```bash
earthengine --project studied-union-325415 task list
```

For a larger model-training run, switch to monthly composites:

```bash
python3 scripts/request_sentinel_gee_exports.py \
  --ee-project studied-union-325415 \
  --frequency monthly
```

Interpretation:

This section supports the transition from the original auditable proxy to true remote sensing. Once the GeoTIFFs arrive from Google Drive, the next pipeline step is to sample each Sentinel composite into the existing H3 grid and use those Sentinel-derived features as the Phase 2 edge-effect inputs.

## Sovereign Compute Nexus Methodology

The SCN workflow is organized into four phases.

### Phase 1: H3 Data Harmonization

Script:

```bash
python3 scripts/build_phase1_h3_baseline.py
```

Purpose:

Convert heterogeneous spatial layers into one H3-indexed table for a 50 km by 50 km Ceara case-study box.

Default case-study parameters:

| Parameter | Value |
| --- | ---: |
| Center latitude | -3.56 |
| Center longitude | -38.82 |
| Box size | 50 km by 50 km |
| H3 resolution | 8 |

Inputs:

- Territorial layers: states, protected lands, indigenous lands
- LULC candidate raster
- Water surface raster
- ONS buses and branches
- IDC point layer
- GEM current and proposed generation context
- ONS curtailment by state

Outputs:

- `clean_data/sovereign_compute_nexus/phase1_h3_baseline.csv`
- `clean_data/sovereign_compute_nexus/phase1_h3_baseline.geojson`
- `clean_data/sovereign_compute_nexus/ceara_case_study_box.geojson`
- `clean_data/sovereign_compute_nexus/phase1_h3_baseline_summary.json`

Findings:

| Metric | Value |
| --- | ---: |
| H3 cells | 2,808 |
| Hard-exclusion cells | 1,686 |
| Protected-overlap cells | 254 |
| Indigenous-overlap cells | 124 |
| Outside-state-boundary cells | 1,353 |
| Non-excluded Phase 1 cells | 1,122 |

Interpretation:

This phase proves the core data-engineering move: energy, water, LULC, topology, data-center, and legal-boundary features can be joined into the same `h3_id` decision table.

### Phase 2: Remote-Sensing Edge-Effect Extraction

Script:

```bash
python3 scripts/run_scn_phase2_rs.py --sentinel-features phase2_rs_features_for_solver.csv
```

Purpose:

Estimate a degradation probability, `p_deg_rs`, for each H3 cell. This is the variable later used in the hard constraint:

```text
P_deg(x) <= epsilon
```

Implementation update:

The original Phase 2 implementation used an auditable local proxy built from H3 fray cells, LULC vulnerability, water sensitivity, distance to protected or indigenous constraints, infrastructure pressure, and a PCA reconstruction-error score. That proxy remains useful as a fallback and for method validation, but the intended Phase 2 workflow has now been advanced to a true remote-sensing extraction pipeline.

To avoid local bandwidth and storage bottlenecks from synchronizing heavy geospatial tensors, the Sentinel extraction is designed to run in a cloud-native Google Colab environment with Google Drive mounted directly. The raw Sentinel-1 and Sentinel-2 Earth Engine exports are read from Drive in place rather than copied through the local workstation. Using the Phase 1 baseline table, each `h3_id` string decision unit is converted back into a precise H3 resolution 8 polygon with the `h3` spatial indexing library.

Completed Sentinel feature source:

```text
phase2_rs_features_for_solver.csv
```

Spatial extraction methodology:

- The pipeline iterates sequentially over H3 geometries instead of loading the full raster stack into memory.
- Each high-resolution GeoTIFF is masked to the current H3 polygon before statistics are computed.
- This zonal extraction pattern keeps memory use bounded for Colab while preserving the 10-meter raster signal inside each H3 decision cell.
- Raster boundary no-data values are explicitly ignored so edge cells do not bias the mean features.

Sensor-specific feature extraction:

| Sensor | Input tensor | Extracted bands/features | H3 statistic |
| --- | --- | --- | --- |
| Sentinel-2 optical | 9-band GeoTIFF composites | `NDVI`, `NDWI` from the exported `B2`, `B3`, `B4`, `B8`, `B11`, `B12`, `NDVI`, `NDWI`, `NBR` stack | Spatial mean of valid 10-meter pixels per H3 cell |
| Sentinel-1 SAR | 4-band GeoTIFF composites | `VV`, `VH` from the exported `VV`, `VH`, `VV_MINUS_VH`, `VV_DIV_VH` stack | Spatial mean of valid 10-meter pixels per H3 cell |

The resulting Sentinel-derived H3 metrics are appended directly to the Phase 1 baseline dataframe and exported as a clean tabular dataset. Methodologically, this replaces the temporary PCA proxy with true optical and radar edge-effect features, while keeping the same solver-facing structure required by Phase 3.

Phase 2 scoring:

The refreshed `p_deg_rs` score now uses Sentinel-derived stress terms when available:

- low Sentinel-2 `NDVI` as optical vegetation stress
- high Sentinel-2 `NDWI` as water/wetness sensitivity
- Sentinel-1 `VV`/`VH` backscatter as radar pressure
- H3 edge exposure, LULC vulnerability, infrastructure pressure, and PCA proxy retained as supporting/fallback terms

Outputs:

- `clean_data/sovereign_compute_nexus/phase2_rs/phase2_rs_edge_effects.csv`
- `clean_data/sovereign_compute_nexus/phase2_rs/phase2_rs_edge_effects.geojson`
- `clean_data/sovereign_compute_nexus/phase2_rs/phase2_rs_features_for_solver.csv`
- `clean_data/sovereign_compute_nexus/phase2_rs/phase2_rs_edge_effect_map.html`

Findings:

| Metric | Value |
| --- | ---: |
| H3 cells | 2,808 |
| Boundary fray cells | 164 |
| Protected fray cells | 125 |
| Indigenous fray cells | 66 |
| Treated edge-proxy cells | 94 |
| Sentinel-matched H3 cells | 2,808 |
| `p_deg_rs` cells from Sentinel optical/SAR extraction | 2,808 |
| PCA training cells | 959 |
| PCA components | 3 |
| Mean `p_deg_rs` | 0.510 |
| P95 `p_deg_rs` | 0.900 |

Interpretation:

Phase 2 now has two evidence levels. The repository-local CSV/GeoJSON outputs preserve the original auditable proxy so the solver remains runnable end-to-end, but the canonical Phase 2 solver table now uses Sentinel-derived H3 features for all 2,808 cells. The upgraded workflow uses Google Drive-mounted Sentinel-1/2 GeoTIFFs, H3 polygon reconstruction, and sequential zonal statistics to produce real optical and radar features for the same decision cells. Phase 3 can consume the refreshed Sentinel-derived `p_deg_rs` inputs without changing the policy-classifier interface.

### Phase 2b: AlphaEarth Embedding Features (learned, below the constraint layer)

`scripts/request_alphaearth_h3_exports.py` asks Earth Engine to average Google's Satellite Embedding V1 (AlphaEarth Foundations, 64-d unit vectors, 10 m, annual from 2017, CC-BY-4.0) over each H3 cell and to compute the mean per-pixel cosine similarity with the previous year. `scripts/ingest_alphaearth_h3.py` merges the Drive CSVs into `clean_data/sovereign_compute_nexus/phase2_rs/alphaearth_h3_<region>_r<res>.parquet`. Phase 2 accepts it via `--alphaearth-features` and, only when `--aef-weight` > 0, blends the cosine-change signal into `rs_sentinel_signal`. The embedding axes are never scored; the hard-constraint and policy layers stay deterministic.

```bash
python3 -m pip install h3 earthengine-api
python3 scripts/request_alphaearth_h3_exports.py --dry-run                      # Ceará box, res 8, 2021-2024
python3 scripts/request_alphaearth_h3_exports.py --ee-project studied-union-325415       # submit ~8 table tasks
python3 scripts/ingest_alphaearth_h3.py --drive-dir "<Drive>/SCN_GEE_AlphaEarth"
python3 scripts/run_scn_phase2_rs.py --sentinel-features phase2_rs_features_for_solver.csv \
    --alphaearth-features clean_data/sovereign_compute_nexus/phase2_rs/alphaearth_h3_ceara_case_study_box_r8.parquet --aef-weight 0.25
```

### Phase 3: Bounded Policy Classifier

Script:

```bash
python3 scripts/run_scn_phase3_policy.py
```

Purpose:

Convert the policy layer into bounded, solver-safe categories. The Phase 3 classifier reads the Phase 2 `p_deg_rs` layer and the local notes PDF:

```text
/Users/nqj5zk/Downloads/67a07e87-6be9-4994-8f8d-3a8f5dd5d7b6_526_Notes.pdf
```

Current implementation:

This is intentionally not an unbounded LLM pipeline. It uses deterministic extraction and rules to produce:

| Category | Weight |
| --- | ---: |
| Low | 1 |
| Medium | 5 |
| Critical | 100 |

The notes PDF triggered five corpus flags:

- SAR is required for all-weather monitoring.
- Causal inference is required to avoid confusing infrastructure effects with drought, fire, or unrelated degradation.
- Bounded schema is required so policy logic does not hallucinate continuous solver values.
- Marco Temporal creates precautionary boundary risk for unhomologated or contested lands.
- Participatory mapping is relevant for boundary gaps and community-identified risk.

Expanded ReData and land-protection corpus:

Phase 3 now also carries a curated 36-document corpus covering ReData, data-center sustainability regulation, indigenous land rights, consultation duties, protected-area law, environmental licensing, and peer-reviewed policy evidence.

Corpus access files:

- `docs/phase3_redata_policy_corpus.md`
- `docs/phase3_redata_policy_corpus.json`
- `clean_data/sovereign_compute_nexus/phase3_policy/phase3_policy_corpus_manifest.json`

Corpus composition:

| Source group | Documents |
| --- | ---: |
| ReData, data-center, and sustainability government sources | 12 |
| Indigenous, traditional-community, protected-area, and environmental-law sources | 15 |
| Peer-reviewed policy/conservation articles | 9 |

Outputs:

- `clean_data/sovereign_compute_nexus/phase3_policy/phase3_policy_penalties.csv`
- `clean_data/sovereign_compute_nexus/phase3_policy/phase2_3_solver_inputs.csv`
- `clean_data/sovereign_compute_nexus/phase3_policy/phase3_policy_penalties.geojson`
- `clean_data/sovereign_compute_nexus/phase3_policy/phase3_policy_map.html`
- `clean_data/sovereign_compute_nexus/phase3_policy/phase3_policy_corpus_manifest.json`

Findings:

| Metric | Value |
| --- | ---: |
| H3 cells | 2,808 |
| Critical cells | 1,734 |
| Medium cells | 365 |
| Low cells | 709 |
| Policy hard-exclusion cells | 1,737 |
| FSOR-allowed cells | 1,071 |
| Human-review cells | 821 |
| `epsilon` | 0.62 |
| Critical `p_deg_rs` threshold | 0.85 |
| Boundary-review buffer | 2.5 km |

Interpretation:

Phase 3 turns qualitative policy concerns into strict, inspectable solver inputs. The important methodological move is that the policy layer cannot invent raw floats. It can only emit categorical bins and hard-exclusion flags.

### Phase 4: Optimization and Pareto Frontier

Final Phase 4 script:

```bash
python3 scripts/run_scn_phase4_optimization.py
```

Purpose:

Run the final constrained H3 siting optimizer using the completed Phase 2 and Phase 3 outputs. Phase 4 enforces hard constraints first, then performs Pareto sorting across the remaining feasible cells.

Hard constraints:

- `fsor_allowed_phase3 == true`
- `policy_hard_exclusion == false`
- `p_deg_rs <= 0.62`
- distance to high-voltage ONS bus <= 25 km
- distance to ONS line <= 15 km
- distance to IDC/fiber anchor <= 50 km

Objectives minimized:

- grid interconnection cost
- IDC latency/proximity cost
- water-land degradation risk
- policy burden
- renewable energy shortfall
- curtailment-opportunity shortfall

Outputs:

- `clean_data/sovereign_compute_nexus/phase4_optimization/phase4_all_h3_scored.csv`
- `clean_data/sovereign_compute_nexus/phase4_optimization/phase4_all_h3_scored.geojson`
- `clean_data/sovereign_compute_nexus/phase4_optimization/phase4_feasible_candidates.csv`
- `clean_data/sovereign_compute_nexus/phase4_optimization/phase4_pareto_frontier.csv`
- `clean_data/sovereign_compute_nexus/phase4_optimization/phase4_pareto_frontier.geojson`
- `clean_data/sovereign_compute_nexus/phase4_optimization/phase4_recommended_sites.csv`
- `clean_data/sovereign_compute_nexus/phase4_optimization/phase4_optimization_map.html`
- `clean_data/sovereign_compute_nexus/phase4_optimization/charts/`

Findings:

| Metric | Value |
| --- | ---: |
| H3 cells | 2,808 |
| Phase 4 feasible cells | 819 |
| Pareto frontier cells | 448 |
| Recommended shortlist cells | 25 |
| Recommended cells requiring human review | 10 |
| Sentinel `p_deg_rs` epsilon | 0.62 |
| Max distance to HV bus | 25 km |
| Max distance to ONS line | 15 km |
| Max distance to IDC/fiber anchor | 50 km |

Interpretation:

Phase 4 completes the end-to-end SCN workflow. The toy MVP is still retained as a proof-of-logic artifact, but the canonical optimizer now uses Sentinel-derived `p_deg_rs`, bounded `lambda_policy`, and `fsor_allowed_phase3` from Phase 3. The recommended shortlist is a screening output, not a permitting conclusion: 10 of the top 25 cells still require human review because they are Medium-stringency cells rather than fully Low-stringency cells.

## Main Maps

The SCN H3 polygon maps now use a docked bottom-third inspector instead of Leaflet popup bubbles. Click a polygon to update the panel with risk, policy, infrastructure, and remote-sensing details while keeping the map context visible.

| Map | Path |
| --- | --- |
| Territorial layers | `clean_data/brazil_territorial_layers_map.html` |
| GEM-style power grid | `clean_data/power_grid/brazil_power_grid_interactive_map.html` |
| Official ONS grid | `clean_data/ons_official_grid/ons_official_grid_interactive_map.html` |
| ONS vs GEM comparison | `clean_data/ons_comparison/ons_vs_synthetic_grid_map.html` |
| ONS curtailment report | `clean_data/ons_curtailment_history/ons_curtailment_history_report.html` |
| ONS detail curtailment report | `clean_data/ons_curtailment_detail/ons_curtailment_detail_report.html` |
| GEM proposed overlay | `clean_data/gem_proposed_overlay/gem_proposed_on_ons_topology_map.html` |
| OSM data centers | `clean_data/osm_data_centers/brazil_osm_data_centers_map.html` |
| IDC points | `clean_data/idc_data_centers/brazil_idc_points_map.html` |
| Integrated DC investment overlay | `clean_data/integrated_dc_investment/brazil_integrated_dc_infrastructure_map.html` |
| LULC and water preview | `clean_data/lulc_water/lulc_water_preview_map.html` |
| SCN MVP Pareto frontier | `clean_data/sovereign_compute_nexus/mvp_pareto/mvp_pareto_frontier_map.html` |
| SCN Phase 2 RS edge effects | `clean_data/sovereign_compute_nexus/phase2_rs/phase2_rs_edge_effect_map.html` |
| SCN Phase 3 policy | `clean_data/sovereign_compute_nexus/phase3_policy/phase3_policy_map.html` |
| SCN Phase 4 constrained optimization | `clean_data/sovereign_compute_nexus/phase4_optimization/phase4_optimization_map.html` |

## Data Conventions

Decisions made on 2026-09-17 while preparing the national expansion. Every new output should follow them.

- **Coordinate reference system: EPSG:4326 (WGS 84).** geobr, ONS, PeeringDB, OSM and the GEE exports already ship in it, and it is what GeoParquet, PMTiles and MapLibre expect. SIRGAS 2000 (EPSG:4674, Brazil's official datum) differs from WGS 84 by centimetres, far below the resolution of any layer here. Call `.to_crs(4326)` explicitly before every write; compute areas and distances in an equal-area projection (e.g. EPSG:5880, SIRGAS 2000 / Brazil Polyconic), never in degrees.
- **Territorial layers: geobr 2025 release only.** States, municipalities, conservation units and Indigenous lands all come from `data/brazil_geospatial/*_2025_simplified.parquet` (Indigenous lands originate from FUNAI, redistributed by IPEA geobr). A FUNAI 2019 shapefile and two single-feature test shapefiles that no script reads were moved to `_archive/superseded_layers_20260917/`.
- **ONS raw data: Parquet only.** ONS publishes early curtailment months only as CSV; `scripts/finalize_ons_data.py` converts them with explicit dtypes (`din_*` -> datetime, `val_*`/`flg_*` -> float, others -> string) and moves the CSV originals to `_archive/`. `data/ons/ons_inventory.json` lists every file with row count and time span.
- **ONS time coverage.** Wind curtailment runs 2021-10 to 2026-05; solar curtailment starts only in 2024-04 because ONS began publishing it then. Daily load (`carga_energia_di`) and hourly subsystem balance (`balanco_energia_subsistema_ho`) cover 2021 to 2025 after running `finalize_ons_data.py`; `build_ons_official_grid.py` and `compare_ons_power_grid.py` still use the 2025 balance alone as a snapshot of the current dispatch mix, which is intentional. Any curtailment-as-share-of-load figure must use matching years.

## Hosting Bundle

`scripts/build_hosting_bundle.py` turns `clean_data/` into `hosting/` (gitignored): state-partitioned GeoParquet for every national vector layer, Parquet tables, a single PMTiles tileset (requires `brew install tippecanoe`), a `manifest.json`, and the dataset card from `docs/hosting_dataset_card.md`. Upload it with `huggingface-cli upload <user>/<repo> hosting . --repo-type dataset`; the map explorer and notebooks then read the layers over HTTP instead of bundling them.

## Reproducibility

Run the major stages in this order:

```bash
python3 scripts/finalize_ons_data.py
python3 scripts/build_brazil_power_grid.py
python3 scripts/build_ons_official_grid.py
python3 scripts/compare_ons_power_grid.py
python3 scripts/analyze_ons_curtailment_history.py
python3 scripts/analyze_ons_curtailment_detail.py
python3 scripts/overlay_gem_proposed_on_ons.py
python3 scripts/download_osm_brazil_data_centers.py
python3 scripts/download_brazil_idc_points.py
python3 scripts/build_integrated_dc_investment_overlay.py
python3 scripts/load_lulc_water_layers.py
python3 scripts/request_sentinel_gee_exports.py --dry-run
python3 scripts/ingest_sentinel_drive_exports.py
python3 scripts/build_phase1_h3_baseline.py
python3 scripts/run_scn_mvp_pareto.py
python3 scripts/run_scn_phase2_rs.py --sentinel-features phase2_rs_features_for_solver.csv
python3 scripts/run_scn_phase3_policy.py --policy-corpus docs/phase3_redata_policy_corpus.json
python3 scripts/run_scn_phase4_optimization.py
python3 scripts/build_hosting_bundle.py
```

After Earth Engine authentication, submit the actual Sentinel export tasks:

```bash
earthengine authenticate
python3 scripts/request_sentinel_gee_exports.py --ee-project studied-union-325415
```

The notebook `preprocessing.ipynb` also contains runnable sections for these steps.

## Important Caveats

- The LULC raster is currently a readable candidate file with a `.crdownload` filename. Replace it with the completed MapBiomas file before publication-grade results.
- Sentinel-1/2 GEE export tasks were submitted under project `studied-union-325415`; check Earth Engine task status before assuming the GeoTIFFs are available in Google Drive. Use the `_v2` Sentinel-2 exports because the first S2 batch failed on mixed band dtypes.
- Phase 2 still retains the auditable proxy artifacts locally, but the report methodology should describe the upgraded Google Colab plus Google Drive Sentinel extraction workflow as the path from proxy features to true remote-sensing inputs.
- Phase 3 is a bounded deterministic classifier, not a full legal RAG system. This is intentional for solver safety.
- ONS topology stress is modeled with DC power-flow approximations, not full AC optimal power flow.
- GEM proposed generation stress is a scenario proxy. It does not include new transmission buildout or market dispatch.
- IDC point data from PeeringDB and OSM is useful for research screening, but facility records are user-maintained and should be validated before business or policy conclusions.
- The state-level investor suitability score is a screening tool, not investment advice. It excludes tariffs, parcel costs, tax incentives, water permits, fiber contracts, interconnection queues, and local permitting timelines.

## Near-Term Research To-Do

1. Replace the candidate LULC file with the completed MapBiomas download.
2. Copy the Colab-extracted Sentinel H3 feature table back into `clean_data/sovereign_compute_nexus/phase2_rs/` and regenerate the Phase 2 solver inputs.
3. Use the Sentinel-2 NDVI/NDWI and Sentinel-1 VV/VH time-series features to train the next convolutional or spatiotemporal autoencoder.
4. Add spatial difference-in-differences once infrastructure timing and pre/post RS outcomes are available.
5. Pull or integrate ISA contested lands, INCRA quilombola territories, ANA watershed/aquifer layers, and ANATEL backhaul data.
6. Upgrade Phase 4 from discrete H3 Pareto screening to a formal Pyomo/Gurobi or BoTorch solver if parcel-level costs, interconnection queues, water permits, and tariff assumptions become available.
7. Keep all final claims separated by evidence level: official ONS, GEM cross-check, OSM/PeeringDB facility proxy, LULC candidate, local RS proxy, Sentinel-extracted RS features, and policy classifier.
