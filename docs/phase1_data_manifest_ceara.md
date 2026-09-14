# Phase 1 Data Manifest: Ceará Case Study

This manifest defines the deterministic empirical inputs for a 50 km x 50 km Ceará case-study grid. It is designed for the Sovereign Compute Nexus pipeline, where every layer is harmonized into H3 cells before ML, RAG, or optimization are allowed to touch the data.

## Case Study Anchor

Default working anchor for the scaffold:

```text
Region: Ceará, Brazil
Approximate industrial/coastal case-study anchor: Pecém / São Gonçalo do Amarante
Center latitude: -3.56
Center longitude: -38.82
Box size: 50 km x 50 km
Default H3 resolution: 8
```

This anchor is only a starting point. Replace it with the exact project coordinate once the case-study site is finalized.

## Deterministic Phase 1 Layers

| Layer | Current local status | Local path | Role |
|---|---:|---|---|
| Brazilian states/municipalities | Available | `clean_data/brazil_territorial_layers.gpkg` | Spatial joins and state attributes |
| FUNAI indigenous lands | Available | `clean_data/brazil_territorial_layers.gpkg` | Hard exclusion / proximity constraint |
| Protected lands | Available | `clean_data/brazil_territorial_layers.gpkg` | Hard exclusion or permit-risk feature |
| ONS substations | Available | `clean_data/ons_official_grid/ons_official_grid_model.gpkg` | Grid access and nearest bus features |
| ONS transmission branches | Available | `clean_data/ons_official_grid/ons_official_grid_model.gpkg` | Grid access and stress features |
| ONS curtailment history | Available | `clean_data/ons_curtailment_history/` | State/region curtailment context |
| GEM current generation | Available | `clean_data/power_grid/brazil_power_grid_model.gpkg` | Existing generation context |
| GEM proposed generation | Available | `clean_data/gem_proposed_overlay/gem_proposed_overlay_model.gpkg` | Pipeline and congestion-stress context |
| IDC/data-center points | Available | `clean_data/idc_data_centers/brazil_idc_points_merged.gpkg` | Existing market/interconnection context |
| LULC candidate raster | Available, partial filename | `clean_data/lulc_water/lulc_candidate_2024.vrt` | Land-cover class per H3 cell |
| Water surface raster | Available | `clean_data/lulc_water/water_surface_2024.vrt` | Water-surface share per H3 cell |

## High-Priority External Layers To Add

| Source | Dataset | Why it matters |
|---|---|---|
| ANA | Watershed boundaries, aquifers, outorgas | Water availability and legal withdrawal constraints |
| ISA | Indigenous and socio-environmental territories | Non-government contested/under-study territory coverage |
| INCRA | Quilombola territories | Traditional community constraints beyond FUNAI |
| ANATEL | Fiber backbones / telecom infrastructure | Latency and interconnection features |
| ANEEL | Wind/solar plant point data | Energy availability and official plant location validation |
| ECOSTRESS | LST and evapotranspiration | High-resolution water/thermal stress |
| Sentinel-1 | SAR time series | Cloud-penetrating structural-change signals |
| Sentinel-2 | Multispectral time series | NDVI/NDWI/NBR and edge degradation signals |

## Phase 1 Output Contract

Primary table:

```text
clean_data/sovereign_compute_nexus/phase1_h3_baseline.csv
```

Primary geospatial layer:

```text
clean_data/sovereign_compute_nexus/phase1_h3_baseline.geojson
```

Required columns:

| Column | Type | Description |
|---|---|---|
| `h3_id` | string | H3 cell id |
| `h3_resolution` | integer | H3 resolution |
| `geometry` | polygon | H3 cell boundary |
| `center_lat` | float | H3 centroid latitude |
| `center_lon` | float | H3 centroid longitude |
| `state_code` | string | Brazilian state abbreviation |
| `in_case_study_box` | bool | Cell is inside 50 km x 50 km box |
| `protected_overlap` | bool | Cell intersects protected land |
| `indigenous_overlap` | bool | Cell intersects indigenous land |
| `hard_exclusion` | bool | Excluded before optimization |
| `dominant_lulc_class` | integer | Dominant LULC class code |
| `water_surface_share` | float | Approximate water raster share |
| `nearest_ons_bus_km` | float | Distance to nearest ONS bus |
| `nearest_hv_ons_bus_km` | float | Distance to nearest >=230 kV ONS bus |
| `nearest_ons_line_km` | float | Distance to nearest ONS branch |
| `nearest_idc_km` | float | Distance to existing IDC/interconnection point |
| `nearby_renewable_mw` | float | Renewable MW within search radius |
| `state_curtailed_mwh` | float | ONS constrained-off context |
| `source_flags` | string/json | Provenance notes |

## Deterministic Rules

1. Protected/indigenous overlaps are not ML outputs.
2. `hard_exclusion` is computed before optimization.
3. LULC and water values are descriptive features, not automatic exclusions unless encoded in Phase 4.
4. Any legal penalty from Phase 3 must be categorical and cited.
5. All distances should be computed in projected CRS, not raw degrees.

## Current Caveat

The local LULC candidate raster is readable, but its source filename is still:

```text
Unconfirmed 614937.crdownload
```

Re-run `scripts/load_lulc_water_layers.py` after the finished LULC file appears so the catalog points to a stable final filename.
