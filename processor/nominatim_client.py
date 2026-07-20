"""Nominatim HTTP client — доступ к Nominatim через HTTP API (REST).

Использует стандартные эндпоинты Nominatim:
  GET /search?q=<query>&format=geojson&limit=<n>
  GET /reverse?lat=<lat>&lon=<lon>&format=geojson&zoom=18

Никакого прямого доступа к PostgreSQL Nominatim — только HTTP API.
"""

import asyncio
import json
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class NominatimClient:
    def __init__(self, config) -> None:
        self._config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = f"http://{config.host}:{config.port}"

    async def connect(self) -> bool:
        try:
            self._session = aiohttp.ClientSession(
                base_url=self._base_url,
                timeout=aiohttp.ClientTimeout(total=self._config.query_timeout),
                headers={"User-Agent": "survival-map-processor/1.0"},
            )
            async with self._session.get(
                "/status", params={"format": "json"}
            ) as resp:
                if resp.status != 200:
                    raise ConnectionError(f"status {resp.status}")
                data = await resp.json()
                logger.info(
                    "Connected to Nominatim HTTP API at %s, status=%s",
                    self._base_url, data.get("status", "?"),
                )
                return True
        except Exception as exc:
            logger.warning("Nominatim HTTP API connection failed: %s", exc)
            await self.close()
            return False

    async def geocode(self, query: str) -> list[dict]:
        if self._session is None or self._session.closed:
            return []
        if not query or len(query) < 3:
            return []
        params = {
            "q": query,
            "format": "geojson",
            "limit": str(self._config.max_results),
        }
        try:
            async with self._session.get(
                self._config.search_path, params=params
            ) as resp:
                if resp.status != 200:
                    logger.debug("Nominatim /search returned %d", resp.status)
                    return []
                body = await resp.json()
            return self._parse_search_response(body)
        except (aiohttp.ClientError, json.JSONDecodeError, asyncio.TimeoutError) as exc:
            logger.debug("Nominatim /search failed: %s", exc)
            return []

    async def reverse(self, lat: float, lon: float) -> Optional[dict]:
        if self._session is None or self._session.closed:
            return None
        params = {
            "lat": str(lat),
            "lon": str(lon),
            "format": "geojson",
            "zoom": "18",
        }
        try:
            async with self._session.get(
                self._config.reverse_path, params=params
            ) as resp:
                if resp.status != 200:
                    return None
                body = await resp.json()
            return self._parse_reverse_response(body)
        except (aiohttp.ClientError, json.JSONDecodeError, asyncio.TimeoutError) as exc:
            logger.debug("Nominatim /reverse failed: %s", exc)
            return None

    @staticmethod
    def _parse_search_response(body: dict) -> list[dict]:
        raw = body.get("features") if isinstance(body, dict) else body
        if not isinstance(raw, list):
            return []
        results = []
        for feat in raw:
            props = feat.get("properties") or {}
            geom = feat.get("geometry")
            if not geom or not geom.get("coordinates"):
                continue
            results.append({
                "place_id": props.get("place_id", 0),
                "osm_type": props.get("osm_type", ""),
                "osm_id": props.get("osm_id", 0),
                "class": props.get("class", ""),
                "type": props.get("type", ""),
                "name": props.get("name") or props.get("display_name", ""),
                "importance": props.get("importance", 0.5),
                "geojson": geom,
                "source": "nominatim",
            })
        return results

    @staticmethod
    def _parse_reverse_response(body: dict) -> Optional[dict]:
        if not isinstance(body, dict) or body.get("type") != "Feature":
            return None
        props = body.get("properties") or {}
        geom = body.get("geometry")
        if not geom or not geom.get("coordinates"):
            return None
        return {
            "place_id": props.get("place_id", 0),
            "osm_type": props.get("osm_type", ""),
            "osm_id": props.get("osm_id", 0),
            "class": props.get("class", ""),
            "type": props.get("type", ""),
            "name": props.get("name") or props.get("display_name", ""),
            "importance": props.get("importance", 0.5),
            "geojson": geom,
            "source": "nominatim",
        }

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            logger.info("Nominatim HTTP session closed")
