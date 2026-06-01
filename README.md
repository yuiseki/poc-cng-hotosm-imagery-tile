# poc-cng-hotosm-imagery-tile

> **A Cloud Native Geospatial PoC: dynamic XYZ raster tiles served from HOTOSM imagery COGs, picked per request via a STAC search API.** No tile pre-build, no preprocessed COG. The tile bytes are read on the fly from whichever Cloud Optimized GeoTIFF the STAC API points at for that tile's bbox.

| | |
| --- | --- |
| **viewer (static)** | https://yuiseki.github.io/poc-cng-hotosm-imagery-tile/ |
| **function (serverless)** | https://hotosm-imagery-tile.yuiseki.com |
| **example tile URL** | https://hotosm-imagery-tile.yuiseki.com/tiles/15/29043/12966.png |
| **STAC source** | https://api.imagery.hotosm.org/stac (OpenAerialMap-backed) |
| **runtime** | FastAPI + rio-tiler + STAC search, Knative-ready |

## Motivation

[poc-cng-cog-tile](../poc-cng-cog-tile) showed that a FaaS function can serve XYZ tiles from a single COG without Martin's GoogleMapsCompatible preprocessing. [study-cng-overture-buildings-tile](../../_study/study-cng-overture-buildings-tile) showed that a STAC catalog can be used as a per-request spatial index over hundreds of cloud-native source files.

This PoC combines the two: instead of a fixed `COG_PATH` env var, the function asks a STAC API "which COG covers this tile?" at request time and pipes the answer into rio-tiler. The result is a raster tile server with no preconfigured dataset. Point it at any STAC API that returns a `visual` COG asset and it serves tiles.

The default STAC API is the HOTOSM OpenAerialMap deployment, which exposes thousands of crowdsourced drone and aerial COGs.

## Architecture

```
                          ┌──────────────────────────────────┐
GET /tiles/{z}/{x}/{y}    │ hotosm-imagery-tile (FastAPI)    │
       │                  │                                  │
       ▼                  │   1. (z,x,y) -> WGS84 bbox       │
       │      POST /search│   2. STAC search by bbox         │  ──►  api.imagery.hotosm.org/stac
       │ ◄────────────────│      (most-recent first)         │
       │                  │   3. pick item.assets.visual COG │
       │                  │   4. rio-tiler /vsicurl/<cog>    │  ──►  oin-hotosm-temp.s3...
       ▼                  │      (window read, no warp)      │
   PNG / WEBP             └──────────────────────────────────┘
```

### Two tile modes

| Endpoint | Mode |
| --- | --- |
| `GET /tiles/{z}/{x}/{y}.png` | Auto-pick: STAC search the tile bbox, render the most recent COG that covers it. Good for "show me whatever imagery exists here". |
| `GET /items/{id}/tiles/{z}/{x}/{y}.png` | Pinned to one STAC item. Good for inspecting a specific dataset, layering, before/after comparisons. |

Both share the same rio-tiler render path, so they produce identical bytes for the same underlying COG.

### Why no startup index

[study-cng-overture-buildings-tile](../../_study/study-cng-overture-buildings-tile) pre-builds a `bbox -> s3` index at startup because Overture's STAC is a static catalog of 512 known Parquet files. A STAC *API* (as opposed to a static catalog) already does that spatial lookup server-side, so the right shape here is to call `/search` per tile rather than mirror the catalog locally. HOTOSM's STAC FastAPI responds in ~100-300ms for a tile-sized bbox, which is acceptable when wrapped in a CDN.

## Endpoints

| Path | Description |
| --- | --- |
| `GET /` | Service info |
| `GET /health` | Health check alias |
| `GET /healthz` | Health check |
| `GET /search?bbox=W,S,E,N&datetime=...&limit=N` | Proxy STAC search, returns a flat item list with `cog` href |
| `GET /items/{id}/metadata` | Open the item's COG and return bounds / zoom / dtype / bands |
| `GET /items/{id}/tilejson.json` | TileJSON 2.2.0 for an item |
| `GET /items/{id}/tiles/{z}/{x}/{y}.{png,webp}` | Render one item |
| `GET /tiles/{z}/{x}/{y}.{png,webp}?datetime=...` | Auto-pick item for tile bbox |

## Configuration

| Env var | Default | Description |
| --- | --- | --- |
| `STAC_API_URL` | `https://api.imagery.hotosm.org/stac` | STAC API root |
| `STAC_ASSET_KEY` | `visual` | Asset key to pick as the COG |
| `STAC_SEARCH_LIMIT` | `50` | Max items per `/search` call |
| `TILE_FORMAT` | `PNG` | Default render format |
| `TILE_SIZE` | `512` | Output tile size in pixels |

Pointing at a different STAC API:

```yaml
environment:
  STAC_API_URL: https://earth-search.aws.element84.com/v1
  STAC_ASSET_KEY: visual
```

## Quick start

```bash
# 1. Build and run
make build
make up-detach

# 2. Smoke test (uses a bbox known to have HOTOSM imagery near Trier, DE)
make verify

# 3. Open the viewer
python3 -m http.server --directory docs 8000
open 'http://localhost:8000/?server=http://localhost:8080'
```

In the viewer, pan to an area, click **search items in view** to list candidate COGs, click one to render it as a raster layer. **show /tiles auto-pick layer** uses the bbox-search endpoint for whatever is currently visible.

## Knative deployment

```bash
# build the image and push it to a registry reachable from every node,
# then apply the Knative Service.
docker build -t 192.168.0.90:5000/hotosm-imagery-tile:0.1.1 -f docker/Dockerfile .
docker push 192.168.0.90:5000/hotosm-imagery-tile:0.1.1
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/ksvc.yaml
kubectl -n knative-pool get ksvc hotosm-imagery-tile
```

The example uses the existing LAN-local registry on `192.168.0.90:5000`, so every node in the cluster can resolve the same image reference without relying on node-local imports.

If your cluster separates control-plane and compute nodes, the service manifest pins the workload to nodes labeled `yuiseki.net/role=compute`.

`min-scale: 0` is fine because the cold start path is just "import rasterio, open one /vsicurl/ COG", not "fetch a 512-item STAC catalog" as in the Overture buildings study. First-tile latency is dominated by the STAC search + COG IFD fetch, both of which are HTTP range reads. `scale-down-delay: 60s` keeps a pod warm during a typical viewer browsing session.

## Repository structure

```
.
├── README.md
├── Makefile
├── docker-compose.yml
├── requirements.txt
├── docker/
│   └── Dockerfile
├── docs/
│   └── index.html              MapLibre viewer (static)
├── k8s/
│   └── ksvc.yaml               Knative Service manifest
├── scripts/
│   └── verify.sh               Smoke test
└── src/
    └── hotosm_imagery_tile/
        ├── __init__.py
        ├── main.py             FastAPI routes + rio-tiler render
        └── stac_client.py      Thin STAC search client
```

## Relationship to neighbouring repos

- [poc-cng-cog-tile](../poc-cng-cog-tile): same tile render core (rio-tiler), but tied to a single fixed COG. Start there if you want to understand the render side in isolation.
- [study-cng-overture-buildings-tile](../../_study/study-cng-overture-buildings-tile): same "STAC as request-time spatial index" idea, but on the *vector* side (DuckDB Spatial against Overture GeoParquet). This PoC is the raster equivalent.

Together they sketch a small CNG function node that can serve dynamic raster + vector tiles from cloud-native primitives without specialized pre-built tilesets.

## Known limitations

- No mosaic: when several HOTOSM items overlap a tile, the most recent one wins. There is no per-pixel blending, so seams are visible at item boundaries.
- No cache layer: each tile request hits STAC search and COG IFD fetch. CDN caching the public URL is recommended.
- Auto-pick assumes the chosen item's COG actually covers the tile pixels. If it only covers a corner, the response is the tile rendered against the available pixels with `nodata` elsewhere; if it is entirely outside, the loop falls through to the next candidate.
- Only `visual` assets are rendered. Multi-band scientific stacks (Sentinel-2 etc.) would need a band-math layer that this PoC does not include.
- Auto-pick `/tiles/{z}/{x}/{y}` skips items whose COG has fewer than 3 bands (e.g. DSM/DTM in float32). Such items often appear paired with RGB visuals in HOTOSM and would otherwise be rendered as grayscale elevation surfaces, which is misleading for a viewer that promises imagery. To render a non-RGB item explicitly, use `/items/{id}/tiles/...`.

## License

Implementation: [MIT](./LICENSE.md) (add a LICENSE file when publishing). HOTOSM imagery is served from OpenAerialMap and is governed by the per-item license in the STAC item properties, typically CC-BY 4.0. Check item metadata before redistributing tiles.
