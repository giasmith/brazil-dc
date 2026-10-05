# Pecém data-center location (inferred)

Status 2026-10-04. No primary document gives the site coordinates; this folder derives them.

**Result:** construction footprint at **3.654 S, 38.820 W** (UTM 24S E520000 N9596100), >= 30 ha,
inside ZPE Setor II of the Complexo do Pecém, Caucaia. Nearest edge ~1.0 km from the APA Lagamar do
Cauípe boundary in the (simplified geobr) CNUC layer; press says ~2 km - verify with the full-resolution boundary. Polygon: `pecem_site_inferred.geojson`.
Treat as inferred until a Semace licence, the ACP 0080626-66.2026.4.05.8100 filing or a ZPE lot map confirms it.

Chain of evidence
1. Press (MPF/DPU release 2026-09-10; Gazeta do Povo; Poder360): ZPE II, ~2 km from APA Lagamar do Cauípe, 34-70 ha.
2. `locate_candidates.py` / `make_map.py`: ring 1.5-2.5 km from the APA using repo CNUC/FUNAI/IBGE layers (2,470 ha).
3. `plan_*.py`: georeferenced the 2022 Plano Diretor (gov_docs/, UTM grid labels, residual <= 6 m) and kept
   CIPP-owned land in that ring -> `pecem_candidate_parcels.geojson` (527 ha Setor II strip + 111 ha port parcel).
4. `scripts/detect_pecem_footprint.py`: Sentinel-2 change detection over the parcels -> `footprint/`.
   fid 13 (30.3 ha) = graded site with building slabs, disturbance from 2025-12/2026-01 (ndvi_timeseries.csv).
   fid 2 (18 ha, port parcel) = migrating dunes, a false positive; ignore.

Caveats: 30 ha is a lower bound (only pixels vegetated in Jul-Dec 2025 count; graded ground continues east
to the road); a dune mask is needed before reusing the detector on the coast; Feb-2025 and Jun-2025 points
in the NDVI series are cloud-affected.
