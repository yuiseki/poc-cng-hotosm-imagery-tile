"""hotosm-imagery-tile: STAC-driven XYZ raster tile server.

Combines two ideas:

- STAC search picks the right COG for a given tile bbox at request time
  (no static pre-built tileset, no `gdalwarp` preprocessing).
- rio-tiler reads only the COG window that the tile needs.

Backed by the HOTOSM OpenAerialMap STAC API by default, but any
STAC API endpoint that returns COG `visual` assets will work.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.io import COGReader

from .stac_client import STACClient, tile_bbox_wgs84

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STAC_API_URL = os.environ.get("STAC_API_URL", "https://api.imagery.hotosm.org/stac")
STAC_ASSET_KEY = os.environ.get("STAC_ASSET_KEY", "visual")
STAC_SEARCH_LIMIT = int(os.environ.get("STAC_SEARCH_LIMIT", "50"))
TILE_FORMAT = os.environ.get("TILE_FORMAT", "PNG")
TILE_SIZE = int(os.environ.get("TILE_SIZE", "512"))

MEDIA_TYPES = {
    "PNG": "image/png",
    "WEBP": "image/webp",
    "JPEG": "image/jpeg",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("hotosm-imagery-tile starting up")
    logger.info("STAC_API_URL: %s", STAC_API_URL)
    logger.info("STAC_ASSET_KEY: %s", STAC_ASSET_KEY)
    logger.info("TILE_FORMAT: %s / TILE_SIZE: %d", TILE_FORMAT, TILE_SIZE)
    app.state.stac = STACClient(
        api_url=STAC_API_URL,
        asset_key=STAC_ASSET_KEY,
        search_limit=STAC_SEARCH_LIMIT,
    )
    try:
        yield
    finally:
        app.state.stac.close()
        logger.info("hotosm-imagery-tile shutting down")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _vsicurl(url: str) -> str:
    """Wrap an https URL so GDAL streams it via /vsicurl/."""
    if url.startswith(("http://", "https://")):
        return f"/vsicurl/{url}"
    return url


def _stac() -> STACClient:
    return app.state.stac


def _health_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "status": "ok",
        "service": "hotosm-imagery-tile",
    }


@app.get("/")
def root():
    return {
        **_health_payload(),
        "stac_api": STAC_API_URL,
        "asset_key": STAC_ASSET_KEY,
        "endpoints": {
            "health": "/health",
            "healthz": "/healthz",
            "search": "/search",
        },
    }


@app.get("/health")
def health():
    return _health_payload()


@app.get("/healthz")
def healthz():
    return _health_payload()


@app.get("/search")
def search(
    bbox: str | None = Query(
        None,
        description="WGS84 bbox as `west,south,east,north`",
    ),
    datetime: str | None = Query(
        None,
        description="RFC3339 single instant or `start/end` interval",
    ),
    limit: int = Query(50, ge=1, le=500),
):
    bbox_tuple = _parse_bbox(bbox) if bbox else None
    try:
        items = _stac().search(bbox=bbox_tuple, datetime=datetime, limit=limit)
    except Exception as exc:
        logger.error("STAC search failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"STAC search failed: {exc}")

    return {
        "count": len(items),
        "items": [_summarize_item(item) for item in items],
    }


@app.get("/items/{item_id}/metadata")
def item_metadata(item_id: str):
    item = _load_item(item_id)
    cog_url = _stac().cog_href(item)
    if not cog_url:
        raise HTTPException(status_code=404, detail="item has no COG asset")
    try:
        with COGReader(_vsicurl(cog_url)) as cog:
            info = cog.info()
            bounds = _bounds_list(cog)
            return {
                "item_id": item_id,
                "cog": cog_url,
                "bounds": bounds,
                "crs": str(info.crs),
                "minzoom": cog.minzoom,
                "maxzoom": cog.maxzoom,
                "dtype": info.dtype,
                "nodata_type": info.nodata_type,
                "colorinterp": info.colorinterp,
                "band_descriptions": info.band_descriptions,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/items/{item_id}/tilejson.json")
def item_tilejson(item_id: str, request: Request):
    item = _load_item(item_id)
    cog_url = _stac().cog_href(item)
    if not cog_url:
        raise HTTPException(status_code=404, detail="item has no COG asset")

    base_url = str(request.base_url).rstrip("/")
    try:
        with COGReader(_vsicurl(cog_url)) as cog:
            bounds = _bounds_list(cog, wgs84=True)
            center_lon = (bounds[0] + bounds[2]) / 2
            center_lat = (bounds[1] + bounds[3]) / 2
            return JSONResponse({
                "tilejson": "2.2.0",
                "name": item.get("id", item_id),
                "description": f"COG tile from {STAC_API_URL} item {item_id}",
                "version": "1.0.0",
                "attribution": item.get("properties", {}).get("license", ""),
                "scheme": "xyz",
                "tiles": [
                    f"{base_url}/items/{item_id}/tiles/{{z}}/{{x}}/{{y}}.{TILE_FORMAT.lower()}"
                ],
                "minzoom": cog.minzoom,
                "maxzoom": cog.maxzoom,
                "bounds": bounds,
                "center": [center_lon, center_lat, cog.minzoom],
                "format": TILE_FORMAT.lower(),
                "tileSize": TILE_SIZE,
            })
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/items/{item_id}/tiles/{z}/{x}/{y}.{fmt}")
def item_tile(item_id: str, z: int, x: int, y: int, fmt: str = "png"):
    item = _load_item(item_id)
    cog_url = _stac().cog_href(item)
    if not cog_url:
        raise HTTPException(status_code=404, detail="item has no COG asset")
    return _render_tile(cog_url, z, x, y, fmt, source_label=item_id)


@app.get("/tiles/{z}/{x}/{y}.{fmt}")
def tile(
    z: int,
    x: int,
    y: int,
    fmt: str = "png",
    datetime: str | None = Query(
        None,
        description="Restrict search to this datetime / interval",
    ),
):
    """Pick the best STAC item for this tile and render it.

    The "best" item is the most recent one whose bbox intersects the
    tile and whose COG actually covers the tile pixels (some items in
    HOTOSM are tiny aerial frames that overlap the tile bbox but only
    cover a corner of the requested tile).
    """
    bbox = tile_bbox_wgs84(z, x, y)
    try:
        items = _stac().search(bbox=bbox, datetime=datetime)
    except Exception as exc:
        logger.error("STAC search failed for tile %d/%d/%d: %s", z, x, y, exc)
        raise HTTPException(status_code=502, detail=f"STAC search failed: {exc}")

    if not items:
        raise HTTPException(status_code=404, detail="no STAC item intersects tile")

    last_error: Exception | None = None
    for item in items:
        cog_url = _stac().cog_href(item)
        if not cog_url:
            continue
        try:
            return _render_tile(
                cog_url, z, x, y, fmt,
                source_label=item.get("id", ""),
                require_rgb=True,
            )
        except (TileOutsideBounds, NotVisualImagery) as exc:
            last_error = exc
            continue
        except Exception as exc:
            last_error = exc
            logger.warning("tile render failed for %s: %s", cog_url, exc)
            continue

    if isinstance(last_error, TileOutsideBounds):
        raise HTTPException(status_code=404, detail="no COG covers this tile")
    if isinstance(last_error, NotVisualImagery):
        raise HTTPException(
            status_code=404,
            detail="no RGB visual COG available for this tile (only non-RGB items found, "
                   "e.g. DSM/DTM). Use /items/<id>/tiles/... to render a specific item.",
        )
    raise HTTPException(
        status_code=500,
        detail=f"all candidate COGs failed: {last_error}",
    )


class NotVisualImagery(Exception):
    """Raised when the COG is not a 3+ band visual RGB image (e.g. DSM/DTM)."""


def _render_tile(
    cog_url: str,
    z: int,
    x: int,
    y: int,
    fmt: str,
    source_label: str,
    require_rgb: bool = False,
) -> Response:
    fmt_upper = fmt.upper()
    media_type = MEDIA_TYPES.get(fmt_upper, "image/png")
    with COGReader(_vsicurl(cog_url)) as cog:
        if require_rgb:
            # Auto-pick path: skip single-band COGs (DSM/DTM rendered as
            # grayscale would mislead viewer users into thinking that's the
            # only imagery available, when an RGB sibling item often exists).
            n_bands = cog.info().count
            if n_bands < 3:
                raise NotVisualImagery(
                    f"{cog_url} has {n_bands} band(s); expected RGB visual"
                )
        img = cog.tile(x, y, z, tilesize=TILE_SIZE)
        data = img.render(img_format=fmt_upper)
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Tile": f"{z}/{x}/{y}",
            "X-STAC-Item": source_label,
        },
    )


def _load_item(item_id: str) -> dict[str, Any]:
    try:
        return _stac().fetch_item(item_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"item not found: {item_id}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"STAC fetch failed: {exc}")


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise HTTPException(
            status_code=400,
            detail="bbox must be `west,south,east,north`",
        )
    try:
        west, south, east, north = (float(p) for p in parts)
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox values must be numeric")
    return (west, south, east, north)


def _summarize_item(item: dict[str, Any]) -> dict[str, Any]:
    props = item.get("properties") or {}
    return {
        "id": item.get("id"),
        "collection": item.get("collection"),
        "bbox": item.get("bbox"),
        "datetime": props.get("datetime")
        or props.get("start_datetime")
        or props.get("end_datetime"),
        "cog": _stac().cog_href(item),
    }


def _bounds_list(cog: COGReader, wgs84: bool = False) -> list[float]:
    bounds = cog.bounds
    if isinstance(bounds, (list, tuple)):
        b = list(bounds)
    else:
        b = [bounds.left, bounds.bottom, bounds.right, bounds.top]
    if not wgs84:
        return b
    # Convert to EPSG:4326 if necessary.
    try:
        from rasterio.warp import transform_bounds
    except ImportError:
        return b
    info = cog.info()
    if str(info.crs).upper() in {"EPSG:4326", "OGC:CRS84"}:
        return b
    west, south, east, north = transform_bounds(info.crs, "EPSG:4326", *b)
    return [west, south, east, north]
