"""Thin HOTOSM STAC API client.

The HOTOSM imagery STAC (https://api.imagery.hotosm.org/stac) is a STAC
FastAPI deployment backed by OpenAerialMap. Unlike a static STAC catalog
(such as Overture), it exposes a search endpoint that is fast enough to
query per tile request, so we do not pre-build an in-memory index at
startup. Items carry a `visual` asset whose href points at a Cloud
Optimized GeoTIFF on the OpenAerialMap S3 bucket.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class STACClient:
    """Minimal client for STAC `/search` and `/collections/.../items/{id}`."""

    def __init__(
        self,
        api_url: str,
        asset_key: str = "visual",
        search_limit: int = 50,
        timeout: float = 15.0,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.asset_key = asset_key
        self.search_limit = search_limit
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def search(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        datetime: str | None = None,
        limit: int | None = None,
        collections: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """POST /stac/search and return the raw item list (newest first)."""
        body: dict[str, Any] = {"limit": limit or self.search_limit}
        if bbox is not None:
            body["bbox"] = list(bbox)
        if datetime is not None:
            body["datetime"] = datetime
        if collections:
            body["collections"] = collections

        url = f"{self.api_url}/search"
        resp = self._client.post(url, json=body)
        resp.raise_for_status()
        payload = resp.json()
        features = payload.get("features", [])
        features.sort(key=_item_datetime_key, reverse=True)
        return features

    def fetch_item(
        self,
        item_id: str,
        collection_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a single item.

        If `collection_id` is known the canonical
        `/collections/{cid}/items/{id}` path is used; otherwise we fall
        back to a `/search` with `ids=` filter.
        """
        if collection_id:
            url = f"{self.api_url}/collections/{collection_id}/items/{item_id}"
            resp = self._client.get(url)
            resp.raise_for_status()
            return resp.json()

        resp = self._client.post(
            f"{self.api_url}/search",
            json={"ids": [item_id], "limit": 1},
        )
        resp.raise_for_status()
        features = resp.json().get("features", [])
        if not features:
            raise KeyError(f"item not found: {item_id}")
        return features[0]

    def cog_href(self, item: dict[str, Any]) -> str | None:
        """Pick the COG href for `item`, preferring https over s3."""
        assets = item.get("assets") or {}
        asset = assets.get(self.asset_key)
        if not asset:
            # Some collections expose the COG under a different key; fall
            # back to the first asset whose type indicates a COG.
            for candidate in assets.values():
                if _is_cog_asset(candidate):
                    asset = candidate
                    break
        if not asset:
            return None

        href = asset.get("href")
        if href and href.startswith(("http://", "https://")):
            return href

        # Some STAC items put the public URL on alternates and the
        # primary href on s3://. rio-tiler can read s3:// when GDAL has
        # AWS credentials, but for an anonymous public bucket the
        # HTTPS URL via /vsicurl/ is more portable.
        alternates = asset.get("alternate") or {}
        for alt in alternates.values():
            alt_href = alt.get("href") if isinstance(alt, dict) else None
            if alt_href and alt_href.startswith(("http://", "https://")):
                return alt_href
        return href


def _item_datetime_key(item: dict[str, Any]) -> str:
    props = item.get("properties") or {}
    return (
        props.get("datetime")
        or props.get("end_datetime")
        or props.get("start_datetime")
        or ""
    )


def _is_cog_asset(asset: dict[str, Any]) -> bool:
    media_type = (asset.get("type") or "").lower()
    if "cloud-optimized" in media_type or "geotiff" in media_type:
        return True
    roles = asset.get("roles") or []
    return any(r in {"data", "visual"} for r in roles)


def tile_bbox_wgs84(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Return the WGS84 bbox for an XYZ tile.

    Uses the standard Web Mercator tiling scheme. Avoids pulling in
    `mercantile` to keep the runtime image small.
    """
    import math

    n = 2 ** z

    def _lon(xi: float) -> float:
        return xi / n * 360.0 - 180.0

    def _lat(yi: float) -> float:
        m = math.pi - 2.0 * math.pi * yi / n
        return math.degrees(math.atan(math.sinh(m)))

    west = _lon(x)
    east = _lon(x + 1)
    north = _lat(y)
    south = _lat(y + 1)
    return (west, south, east, north)
