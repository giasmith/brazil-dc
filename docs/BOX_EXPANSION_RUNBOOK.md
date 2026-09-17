# Expanding the Ceará study box to 75 × 55 km

Prepared September 17, 2026. Purpose: enlarge the case-study extent so the Fortaleza
data centers — six commissioned and one planned — fall inside the analysis area instead of
13–16 km outside its eastern edge.

## The new extent

The box is anchored on the old one: the **west and north edges do not move**, and the box grows
east and south. Everything is computed in EPSG:3857, exactly as `case_study_box()` has always
done it.

| | old | new |
|---|---|---|
| center (lat, lon) | −3.56, −38.82 | **−3.5824142723, −38.7077105895** |
| size (projected) | 50 × 50 km | **75 km E–W × 55 km N–S** |
| longitude | −39.044579 … −38.595421 | **−39.044579 … −38.370842** |
| latitude | −3.784118 … −3.335828 | **−3.828934 … −3.335828** |
| ground size at mid-latitude | 49.90 × 49.90 km | 74.85 × 54.89 km |
| H3 r8 cells | 2,808 | ≈ 4,600 (estimate, scaled by area) |
| Ceará land cells | 1,455 | ≈ 2,000 (estimate) |
| Fortaleza facilities inside | 0 of 7 | 7 of 7, ≥ 4.9 km from any edge |

The center coordinates are given to ten decimal places on purpose. At that precision the new
west and north edges land on the old ones to within a fraction of a millimeter; rounding to six
decimals moves them by about 4 cm, which is harmless for a 460 m hexagon but makes the two runs
harder to compare.

Note for the write-up: because the box is built in Web Mercator, the "50 km" square was really
49.90 km on the ground at this latitude, and the new box is 74.85 × 54.89 km. Worth a footnote if
a reviewer measures it.

## Code changes already made

Two scripts were patched in place. Originals are in `_archive/prebox_expansion_20260917/`.

`scripts/build_phase1_h3_baseline.py` now accepts a rectangle. `case_study_box()` and
`build_h3_grid()` take separate east–west and north–south extents, a new `box_dimensions()`
helper resolves the CLI flags and rejects non-positive sizes, and the summary records
`box_km_ew` and `box_km_ns` (`box_km` stays, and is null when the box is not square). Two new
flags, `--box-km-ew` and `--box-km-ns`, override `--box-km`; with neither one given the script
behaves exactly as before. Verified: `--box-km 50` still reproduces the committed
`ceara_case_study_box.geojson` to 1.2e-14 degrees.

`scripts/run_scn_phase2_rs.py` now reports and can refuse missing remote-sensing coverage. This
matters because of a trap worth knowing about: `normalize_minimize()` fills missing values with
the **95th percentile** of the observed distribution rather than leaving them blank. A cell with no
Sentinel row therefore scores as one of the greenest, least-stressed cells in the box, sails
under the p_deg ceiling, and can win a shortlist slot on imputed data that looks exactly like
measured data. If Phase 1 is rerun on the bigger box and Phase 2 is then run against the old
feature table, every one of the roughly 1,800 new cells — all of them in and around Fortaleza —
would be scored that way.

So the Phase 2 summary now carries `unmatched_h3_cells` and a note explaining the fill, and a new
`--require-rs-coverage` flag stops the run instead of imputing. Default behavior is unchanged, so
the existing results still reproduce.

Two further defects turned up while tracing what the enlarged grid would do, both now fixed.

`scripts/run_scn_phase4_optimization.py` crashed in any run without a Sentinel feature table.
`gdf.get("rs_sentinel_signal", 0)` returns a bare int when the column is absent, and an int has no
`.fillna`, so `--force-proxy` died with an AttributeError. A missing Sentinel signal is now scored
neutral at 0.5 — the same value a NaN already received — rather than 0, which would have read as
the best possible signal.

`scripts/run_scn_phase3_policy.py` carried the falsy-zero trap again, this time in the reason text:
`float(row.get("nearest_constraint_km", 9999) or 9999)` turns a cell sitting exactly on a
conservation-unit or indigenous boundary into one 9,999 km away, because 0.0 is falsy. All 218
cells at 0.0 km in the current run are missing `within_boundary_review_buffer` from their stated
`policy_reason`. The review flag itself was always right — it comes from the vectorised path in
`classify_policy()`, which uses `fillna(9999)` correctly — so no cell was misclassified, and every
one of the 218 has another reason listed. It is a reporting gap, not a decision error, but it would
have grown in the enlarged box where more boundaries fall inside the frame. Phase 3 now has the
same `as_float()` helper Phase 4 got after the 0 km fix.

## Running it today without waiting for Earth Engine

`--force-proxy` skips the Sentinel and AlphaEarth tables entirely and derives p_deg from the local
proxy terms only: edge exposure, PCA reconstruction error, LULC vulnerability, infrastructure
pressure and surface water. Every cell is then scored the same way, so there is no imputation
asymmetry between old and new cells and the run is internally consistent and defensible. What it is
not is comparable to the published p_deg values, which carry the Sentinel terms. To compare like
with like, archive the outputs and rerun the 50 km box with `--force-proxy` as well.

## Order of operations

**1. Rebuild the Phase 1 grid on the new extent.**

```bash
cd ~/Projects/Brazil
python3 scripts/build_phase1_h3_baseline.py \
  --center-lat -3.5824142723 --center-lon -38.7077105895 \
  --box-km-ew 75 --box-km-ns 55
```

This overwrites `clean_data/sovereign_compute_nexus/phase1_h3_baseline.{csv,geojson}`,
`phase1_h3_baseline_summary.json`, and `ceara_case_study_box.geojson`. The archived copies of the
box and summary are already saved. Check the new `h3_cell_count` against the ≈ 4,600 estimate
before going further — a wildly different number means something went wrong.

**2. Submit the Earth Engine exports for the new box.** Both request scripts read
`ceara_case_study_box.geojson`, so they pick up the new extent automatically once step 1 has run.

```bash
python3 scripts/request_sentinel_gee_exports.py --dry-run     # inspect the manifest first
python3 scripts/request_sentinel_gee_exports.py
python3 scripts/request_alphaearth_h3_exports.py --dry-run
python3 scripts/request_alphaearth_h3_exports.py
```

This is the long pole and it needs your Earth Engine credentials (project `edwc-483823`) and your
Drive. The AlphaEarth request chunks at 2,000 cells per task, so the cell count above means about
three tasks per year instead of two. Tasks then run for hours.

**3. Ingest the exports once Drive has synced them.**

```bash
python3 scripts/ingest_sentinel_drive_exports.py --drive-dir "<your gee_ceara Drive folder>"
python3 scripts/ingest_alphaearth_h3.py --drive-dir "<your Drive folder>" \
  --region ceara_case_study_box --h3-resolution 8
```

Worth checking here: `data/gee/sentinel_drive_ingest_summary.json` from the last run reported
38 expected files and 0 accepted, and only one GeoTIFF is on disk
(`scn_ceara_s2_20260101_20260401_v2.tif`). The current `phase2_rs_features_for_solver.csv` has a
single dated Sentinel-2 column, `ndvi_s2_20260101_20260401`, so the published p_deg rests on one
quarter (January–April 2026) rather than the quarterly series from 2021 the manifest requests. If
the paper says quarterly composites over several years, that sentence needs either more ingested
quarters or a correction.

**4. Rerun Phases 2, 3 and 4**, with the guard on so a coverage gap fails loudly:

```bash
python3 scripts/run_scn_phase2_rs.py --require-rs-coverage \
  --alphaearth-features clean_data/sovereign_compute_nexus/phase2_rs/aef_h3_features.parquet
python3 scripts/run_scn_phase3_policy.py
python3 scripts/run_scn_phase4_optimization.py
```

Use the same `--aef-weight` as the published run so the only thing that changed is the extent.

**5. Rebuild the explorer and the documents.**

```bash
python3 map_explorer/build_data.py
```

Then the figures (`make_maps.py`, `make_funnel.py`, `make_pareto.py`) and the numbers in
`docs/Brazil_SCN_Framework_v2.docx`, `docs/Brazil_SCN_Progress_Memo.docx` and
`docs/SCN_Phases_and_Data.pptx`. Every headline figure changes: 2,808 cells, 966 feasible, 505 on
the frontier, 25 recommended, the 12 review cells, and the 74.33 top score are all specific to the
50 km box. The explorer's hard-coded strings ("2,808 H3 cells · 50 km × 50 km box", the study-box
overlay label, "355 cells sit exactly at the 0.90 ceiling") need updating too.

## What to expect from the results

The added area is the Fortaleza metro: dense urban fabric, the Tapeba indigenous land, and the
coastal conservation units. Most of it should classify as high-degradation or policy-critical, so
the new cells will mostly *not* survive into the shortlist. That is a legitimate finding rather
than a disappointment — it shows the existing facilities cluster where the model would not site a
new one, which is a sharper argument than the current framing that the box simply excludes them.
Expect the feasible count to rise roughly in proportion to land area while the recommended 25 stay
in the Pecém corridor.

## Still open from earlier

The folium inspector map `phase4_optimization_map.html` is still the pre-fix run and needs
geopandas to regenerate. Reference [36] in the paper has a stray line break.
