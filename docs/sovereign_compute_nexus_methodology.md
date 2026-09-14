# Sovereign Compute Nexus Methodology

This document turns the Sovereign Compute Nexus idea into a reproducible research pipeline. The core rule is simple: deterministic spatial harmonization and strict optimization remain inspectable and owned by the researcher; ML and RAG components only produce bounded intermediate variables that can be audited before they enter the solver.

## Research Object

The framework evaluates candidate data-center siting cells under the Energy-Water-Land Trilemma and indigenous/traditional-territory constraints.

The atomic decision unit is an H3 cell:

```text
x = one H3 cell at a chosen resolution, e.g. H3 resolution 8
```

Every variable used by the optimization core must be indexed by `h3_id`.

## Phase Flowchart

```mermaid
flowchart TD
    A[Raw empirical data] --> A1[Remote sensing rasters]
    A --> A2[Vector boundaries]
    A --> A3[Energy and transmission topology]
    A --> A4[IDC and telecom infrastructure]

    A1 --> B[Phase 1: Data Harmonization]
    A2 --> B
    A3 --> B
    A4 --> B

    B --> B1[H3 spatial baseline]
    B1 --> B2[base_cost_x]
    B1 --> B3[latency_proxy_x]
    B1 --> B4[water_surface_x]
    B1 --> B5[renewable_access_x]
    B1 --> B6[hard_exclusion_flags_x]

    B1 --> C[Phase 2: Socio-Ecological Modeling]
    C --> C1[Edge-effect autoencoder]
    C --> C2[Water/LST forecasting]
    C1 --> C3[P_deg_x]
    C2 --> C4[tau_t seasonal water limit]

    B1 --> D[Phase 3: Context Engine]
    D --> D1[Legal corpus ingestion]
    D1 --> D2[Bounded RAG classification]
    D2 --> D3[lambda_policy_x in {1,5,100}]
    D2 --> D4[boundary compliance rules]

    B1 --> E[Phase 4: Optimization Core]
    C3 --> E
    C4 --> E
    D3 --> E
    D4 --> E

    E --> F[Constrained Bayesian Optimization / MILP]
    F --> G[Pareto-optimal compliant siting frontier]
```

## Phase 1: Data Harmonization

Goal: convert heterogeneous raw files into one H3-indexed baseline table.

Inputs already present in this project:

- ONS official substations and transmission topology.
- ONS constrained-off and curtailment summaries.
- GEM current and proposed generation assets.
- Brazilian states, municipalities, protected lands, and FUNAI indigenous lands.
- PeeringDB and OSM IDC/data-center point layers.
- MapBiomas-style LULC candidate raster and 2024 water-surface raster.

Target output:

```text
clean_data/sovereign_compute_nexus/phase1_h3_baseline.csv
```

Each row should contain:

| Field | Meaning |
|---|---|
| `h3_id` | H3 cell id |
| `state_code` | Brazilian state abbreviation |
| `protected_overlap` | Boolean/proportion of overlap with protected land |
| `indigenous_overlap` | Boolean/proportion of overlap with indigenous land |
| `water_surface_share` | Water raster share inside/near cell |
| `dominant_lulc_class` | Dominant MapBiomas class code |
| `nearest_substation_km` | Distance to nearest ONS bus/substation |
| `nearest_hv_substation_km` | Distance to nearest >=230 kV ONS bus |
| `nearest_transmission_km` | Distance to nearest ONS line |
| `nearest_idc_km` | Distance to nearest existing IDC/interconnection facility |
| `renewable_capacity_nearby_mw` | ONS/GEM clean capacity around the cell |
| `curtailment_context_mwh` | State or plant-level curtailment context |
| `hard_exclusion` | Deterministic exclusion flag |

## Phase 2: Socio-Ecological Modeling

Phase 2 should never directly decide sites. It only produces bounded, auditable model outputs.

### Edge-Effect Classifier

Input tensor:

```text
X[h3_id, time, band, row, col]
```

Suggested bands:

- Sentinel-2 NDVI, NDWI, NBR.
- Sentinel-1 VV/VH texture.
- LULC transition indicators.

Output:

```text
P_deg(x) in [0, 1]
```

This is the probability-like degradation gradient used later as a hard or soft constraint.

Current local runner:

```bash
python3 scripts/run_scn_phase2_rs.py
```

This produces an auditable local proxy named `p_deg_rs` using H3 fray cells, MapBiomas LULC, water presence, protected/indigenous proximity, and infrastructure pressure. Because raw Sentinel tensors are not yet loaded, the script uses a PCA reconstruction-error proxy rather than the final convolutional autoencoder. The output is solver-ready, but should be described as a Phase 2 scaffold until Sentinel-1/2 time series are added.

Default outputs:

- `clean_data/sovereign_compute_nexus/phase2_rs/phase2_rs_edge_effects.csv`
- `clean_data/sovereign_compute_nexus/phase2_rs/phase2_rs_edge_effects.geojson`
- `clean_data/sovereign_compute_nexus/phase2_rs/phase2_rs_features_for_solver.csv`
- `clean_data/sovereign_compute_nexus/phase2_rs/phase2_rs_edge_effect_map.html`

### Seasonal Water Stress Forecast

Input:

- Water surface time series.
- ECOSTRESS LST/ET when available.
- Rainfall/evapotranspiration covariates.

Output:

```text
tau(x, t)
```

`tau` represents seasonal feasible operating-region contraction, not a siting score by itself.

## Phase 3: Context Engine

The RAG/policy system must be schema-bounded. It should return categories and citations, not free-form numeric values.

Allowed legal stringency values:

```text
Low -> 1
Medium -> 5
Critical -> 100
```

Required output schema:

```json
{
  "region_id": "string",
  "legal_stringency": "Low|Medium|Critical",
  "penalty_weight": 1,
  "hard_exclusion": false,
  "citations": [
    {
      "document_id": "string",
      "page": 1,
      "quoted_span": "short quote"
    }
  ],
  "rationale": "short explanation"
}
```

Current local runner:

```bash
python3 scripts/run_scn_phase3_policy.py
```

This reads the Phase 2 `p_deg_rs` layer and the local notes PDF, then applies a deterministic bounded classifier. It does not allow the policy layer to invent continuous values. It emits only `Low`, `Medium`, or `Critical`, with fixed weights of `1`, `5`, and `100`, plus `fsor_allowed_phase3` and `human_review_required`.

Default outputs:

- `clean_data/sovereign_compute_nexus/phase3_policy/phase3_policy_penalties.csv`
- `clean_data/sovereign_compute_nexus/phase3_policy/phase2_3_solver_inputs.csv`
- `clean_data/sovereign_compute_nexus/phase3_policy/phase3_policy_penalties.geojson`
- `clean_data/sovereign_compute_nexus/phase3_policy/phase3_policy_map.html`
- `clean_data/sovereign_compute_nexus/phase3_policy/phase3_policy_corpus_manifest.json`

## Phase 4: Optimization Core

The optimization model should separate objectives from hard constraints.

Example hard constraints:

```text
x not in indigenous_lands
x not in protected_lands requiring absolute exclusion
P_deg(x) <= epsilon
water_demand(x, t) <= tau(x, t)
grid_interconnection_required_mw <= available_capacity(x)
```

Example objectives:

```text
minimize latency_proxy(x)
minimize transmission_upgrade_cost(x)
minimize water_risk(x)
maximize clean_power_access(x)
maximize legal_compliance_margin(x)
```

Output:

```text
Pareto frontier of compliant candidate H3 cells
```

## 48-Hour MVP Track

The repo now includes a deliberately bounded proof-of-logic runner:

```bash
python3 scripts/run_scn_mvp_pareto.py
```

This is not the full dissertation pipeline. It uses the Phase 1 Ceará H3 baseline, creates a deterministic mock Phase 2 degradation probability named `synthetic_p_deg`, applies hard exclusions for protected lands, indigenous lands, offshore cells, and water cells, then computes a nondominated Pareto frontier across grid cost, latency, water-land risk, renewable opportunity, and curtailment opportunity.

Use this MVP for preliminary figures, advisor conversations, and conference-method sketches. Replace `synthetic_p_deg` with the trained remote-sensing model before making empirical claims about ecological degradation.

Default outputs:

- `clean_data/sovereign_compute_nexus/mvp_pareto/mvp_feasible_candidates.csv`
- `clean_data/sovereign_compute_nexus/mvp_pareto/mvp_pareto_frontier.csv`
- `clean_data/sovereign_compute_nexus/mvp_pareto/mvp_pareto_frontier.geojson`
- `clean_data/sovereign_compute_nexus/mvp_pareto/mvp_pareto_frontier_map.html`
- `clean_data/sovereign_compute_nexus/mvp_pareto/charts/`

## Current Repo Status

The local project already has enough to begin Phase 1:

- `clean_data/integrated_dc_investment/brazil_integrated_dc_layers.gpkg`
- `clean_data/lulc_water/lulc_candidate_2024.vrt`
- `clean_data/lulc_water/water_surface_2024.vrt`
- `clean_data/ons_official_grid/ons_official_grid_model.gpkg`
- `clean_data/gem_proposed_overlay/gem_proposed_overlay_model.gpkg`
- `clean_data/idc_data_centers/brazil_idc_points_merged.gpkg`

Missing or intentionally deferred:

- `h3` Python package for Phase 1 H3 cell generation.
- `torch` for Phase 2 autoencoder scaffolding.
- `langchain` / OpenAI client stack for Phase 3 RAG.
- `pyomo`, `gurobipy`, or BoTorch for Phase 4 optimization.

## Guardrails

- Do not let an LLM invent continuous penalty weights.
- Do not let model scores override legal hard exclusions.
- Keep every intermediate indexed by `h3_id`.
- Store source provenance for every variable used by the solver.
- Treat current LULC as candidate until the `.crdownload` source is replaced by the finished file.
