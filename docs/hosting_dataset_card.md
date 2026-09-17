---
license: cc-by-4.0
language:
  - en
  - pt
pretty_name: Brazil Sovereign Compute Nexus - geospatial layers
tags:
  - geospatial
  - geoparquet
  - pmtiles
  - brazil
  - energy
  - data-centers
  - remote-sensing
size_categories:
  - 10K<n<100K
---

# Brazil Sovereign Compute Nexus: hosted data layers

Static, cloud-native copies of the layers behind the
[Brazil data-center siting pipeline](https://github.com/giasmith/brazil-dc) and its
[interactive map](https://giasmith.github.io/brazil-dc/map_explorer/). Everything is
**EPSG:4326**, and every vector layer is **GeoParquet, hive-partitioned by state**
(`uf=CE/`, `uf=SP/`, ...), so a client can fetch one state without downloading Brazil.
`manifest.json` lists every layer with row counts, bounding box, columns, source and license.

## Layout

```
manifest.json
geoparquet/<layer>/uf=<UF>/part.parquet   # vector layers (see manifest for the list)
tables/<name>.parquet                     # non-spatial tables (curtailment, suitability, ...)
pmtiles/brazil_dc.pmtiles                 # all vector layers as one tileset for web maps
```

`uf=XX` holds features that fall outside every state polygon (offshore wind, bad coordinates).
The state code is only in the folder name (hive style); readers that understand hive
partitioning (DuckDB, pyarrow, GeoPandas on a directory) reconstruct the `uf` column.

## Reading the data

Python, one state:

```python
import geopandas as gpd
gdf = gpd.read_parquet("hf://datasets/<user>/<repo>/geoparquet/ons_buses/uf=CE/part.parquet")
```

DuckDB, whole country, no download:

```sql
INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;
SELECT uf, count(*) AS buses
FROM read_parquet('hf://datasets/<user>/<repo>/geoparquet/ons_buses/*/*.parquet', hive_partitioning = true)
GROUP BY uf ORDER BY buses DESC;
```

Web map (MapLibre GL JS + the `pmtiles` protocol):

```js
const p = new pmtiles.Protocol(); maplibregl.addProtocol("pmtiles", p.tile);
map.addSource("brazil", { type: "vector", url: "pmtiles://https://huggingface.co/datasets/<user>/<repo>/resolve/main/pmtiles/brazil_dc.pmtiles" });
map.addLayer({ id: "buses", type: "circle", source: "brazil", "source-layer": "ons_buses" });
```

## Coverage

| Layer group | Coverage |
| --- | --- |
| Territorial (states, municipalities, conservation units, Indigenous lands) | National, IPEA geobr 2025 release |
| ONS grid (substations/buses, transmission branches), load, curtailment | National, 2021-10 to 2026-05 (solar curtailment from 2024-04) |
| Generators (Global Energy Monitor, current and proposed) | National |
| Data centers (PeeringDB + OpenStreetMap) | National; OSM coverage is sparse outside SP/RJ/CE |
| H3 siting outputs (Phase 4 scored cells, Pareto frontier, recommended sites) | **Ceará 50 km case-study box only** (`uf=CE`); national runs will be added as further partitions |

## Sources and licenses

Each layer's `source` and `license` fields in `manifest.json` are authoritative. In short:
IBGE/FUNAI/MMA layers via IPEA geobr (CC-BY-4.0); ONS open data under the ONS open-data
terms; Global Energy Monitor (CC-BY-4.0); PeeringDB public facility file (PeeringDB terms);
OpenStreetMap features (ODbL, © OpenStreetMap contributors); derived layers from this pipeline (CC-BY-4.0).
Derived layers are research screening outputs, not investment or permitting advice.

## Citation

Smith, G. (2026). *Brazil Sovereign Compute Nexus: hosted data layers* (version v1). Hugging Face.
See the GitHub repository for the accompanying methodology note and the Zenodo DOI of the matching code release.
