# Codex Sprint Prompts for Phases 2 and 3

Use these prompts when you are ready to generate boilerplate. They are intentionally strict: Codex may write scaffolding, but it should not decide your assumptions, weights, or legal thresholds.

## Phase 2A: Edge-Effect Autoencoder

Prompt:

```text
Write a PyTorch training script for a convolutional autoencoder that ingests H3-indexed spatiotemporal tensors for the Sovereign Compute Nexus project.

Input contract:
- A parquet file with columns: h3_id, date, band, patch_row, patch_col, value.
- Bands may include Sentinel-2 NDVI, NDWI, NBR, red, nir, swir1, swir2 and Sentinel-1 VV/VH.
- The loader must pivot records into tensors with shape [batch, time, bands, height, width].
- Missing values must be masked, not silently filled with zero.

Architecture:
- Encoder: 3D convolution blocks over [time, height, width].
- Bottleneck dimension must be a CLI argument.
- Decoder: mirrored 3D transpose-convolution blocks.
- Loss: masked MSE reconstruction loss.
- Output: per-h3 reconstruction error table with columns h3_id, date, reconstruction_error, p_deg_proxy.

Constraints:
- Do not invent environmental thresholds.
- Do not classify cells as suitable/unsuitable.
- Save model config, random seed, training metrics, and data manifest as JSON.
- Include unit-testable functions for tensor assembly and masked loss.
```

Expected output path:

```text
scripts/train_edge_effect_autoencoder.py
clean_data/sovereign_compute_nexus/phase2_degradation_gradient.csv
```

## Phase 2B: Causal Inference / Difference-in-Differences

Prompt:

```text
Write a Python scaffold for a spatial Difference-in-Differences model using DoWhy or EconML.

Dataset contract:
- Input table is H3-indexed panel data with h3_id, date, treatment, outcome_ndvi, rainfall, lst, water_surface_share, state_code, distance_to_infrastructure_km.
- Treatment is presence or activation of data-center infrastructure.
- Outcome is NDVI degradation or edge-effect reconstruction error.

Model requirements:
- Include fixed effects for h3_id and time.
- Include spatial buffer controls: treated cells, near-neighbor cells, and distant controls.
- Include placebo timing hooks.
- Include pre-trend diagnostic plots.
- Output estimated treatment effect with confidence intervals and diagnostics.

Constraints:
- Do not claim causality unless pre-trend and placebo diagnostics pass.
- Do not overwrite Phase 1 hard-exclusion flags.
```

Expected output path:

```text
scripts/run_spatial_did.py
clean_data/sovereign_compute_nexus/phase2_causal_effects.csv
```

## Phase 2C: Water/LST Forecasting

Prompt:

```text
Write a PyTorch Lightning or plain PyTorch script for seasonal water-stress forecasting on H3 cells.

Input:
- H3 panel table with monthly water_surface_share, LST, ET, rainfall, drought index, and state_code.
- Target is next-month water_surface_share or water stress class.

Architecture options:
- Implement a baseline LSTM first.
- Add a graph-ready interface where edges are neighboring H3 cells.

Output:
- tau_hydrological_limit table with h3_id, month, predicted_water_stress, tau, uncertainty.

Constraints:
- tau must be generated from an explicit researcher-defined function, not an LLM.
- Include backtesting split by time, not random row split.
```

Expected output path:

```text
scripts/forecast_water_stress.py
clean_data/sovereign_compute_nexus/phase2_tau_water_limits.csv
```

## Phase 3A: Legal RAG With Bounded Output

Prompt:

```text
Write a Python script using LangChain and OpenAI's API to build a Retrieval-Augmented Generation pipeline for Brazilian environmental, telecom, and indigenous-rights legal documents.

Input:
- PDF folder path supplied by CLI.
- Region/H3 metadata table with h3_id, municipality, state_code, protected_overlap, indigenous_overlap, quilombola_overlap.

Retrieval:
- Use chunked PDF text with page numbers preserved.
- Use vector retrieval only to collect evidence.
- The LLM must output a strict Pydantic schema.

Schema:
class LegalAssessment(BaseModel):
    h3_id: str
    legal_stringency: Literal["Low", "Medium", "Critical"]
    penalty_weight: Literal[1, 5, 100]
    hard_exclusion: bool
    cited_documents: list[Citation]
    rationale: str

class Citation(BaseModel):
    document_id: str
    page: int | None
    quoted_span: str

Rules:
- If indigenous_overlap or protected hard-exclusion flag is true, hard_exclusion must remain true.
- The LLM may not invent float weights.
- The LLM may not output categories outside Low, Medium, Critical.
- Every non-Low classification must include at least one citation.
- Failed schema validation must trigger retry, then quarantine row.
```

Expected output path:

```text
scripts/build_legal_context_engine.py
clean_data/sovereign_compute_nexus/phase3_policy_penalties.csv
```

## Phase 3B: Legal Corpus Manifest

Prompt:

```text
Write a legal corpus ingestion manifest builder.

For every PDF in the input folder, extract:
- document_id
- filename
- title if available
- source_url if provided in sidecar metadata
- jurisdiction
- date
- document_type
- sha256 hash
- page_count

Save a CSV and JSON manifest. Do not parse legal meaning in this script.
```

Expected output path:

```text
scripts/build_legal_corpus_manifest.py
clean_data/sovereign_compute_nexus/legal_corpus_manifest.csv
```

## Phase 4 Interface Contract

Phases 2 and 3 must produce these exact files before the optimization core runs:

```text
phase1_h3_baseline.csv
phase2_degradation_gradient.csv
phase2_tau_water_limits.csv
phase3_policy_penalties.csv
```

The solver should join by `h3_id` and reject rows with missing hard-constraint fields.
