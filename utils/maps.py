import re
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote

import httpx

from models.location import MapLocation

# Hosts allowed for short-URL resolution (allowlist prevents SSRF)
_ALLOWED_SHORT_HOSTS = {"maps.app.goo.gl", "goo.gl"}

# Private/reserved IP ranges – reject redirects pointing here (IPv4 + IPv6)
_PRIVATE_IP_RE = re.compile(
    r"^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|127\.|169\.254\.|localhost"
    r"|::1|fe80:|fc00:|fd00:)",
    re.IGNORECASE,
)


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        return not _PRIVATE_IP_RE.match(host)
    except Exception:
        return False


def parse_maps_url(url: str) -> Optional[MapLocation]:
    """
    Extract lat/lng from common Google Maps URL formats.
    Returns None if no coordinates can be extracted.
    """
    if not url:
        return None

    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        path = unquote(parsed.path)

        # Format: ...@lat,lng,zoom...
        at_match = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
        if at_match:
            lat, lng = float(at_match.group(1)), float(at_match.group(2))
            return _build_location(lat, lng, url)

        # Format: !3d<lat>!4d<lng>
        d3_match = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", url)
        if d3_match:
            lat, lng = float(d3_match.group(1)), float(d3_match.group(2))
            return _build_location(lat, lng, url)

        # Format: ?q=lat,lng or /search/lat,lng or /place/...
        q_values = query.get("q", []) + query.get("query", [])
        for qv in q_values:
            coord_match = re.match(r"^(-?\d+\.\d+),(-?\d+\.\d+)$", qv.strip())
            if coord_match:
                lat, lng = float(coord_match.group(1)), float(coord_match.group(2))
                return _build_location(lat, lng, url)
            # place_id: prefix
            if qv.startswith("place_id:"):
                place_id = qv.split(":", 1)[1]
                return MapLocation(place_id=place_id, maps_url=url, source="url_paste")

        # Format: /place/Name/@lat,lng (already caught above via @) – fallback for address
        # Format: ll=lat,lng param
        ll_values = query.get("ll", [])
        for ll in ll_values:
            coord_match = re.match(r"^(-?\d+\.\d+),(-?\d+\.\d+)$", ll.strip())
            if coord_match:
                lat, lng = float(coord_match.group(1)), float(coord_match.group(2))
                return _build_location(lat, lng, url)

        # Format: /maps/place/Name/ — extract place name as address
        place_match = re.search(r"/maps/place/([^/@]+)", path)
        if place_match:
            name = place_match.group(1).replace("+", " ").replace("%20", " ")
            if name and not name.startswith("?"):
                return MapLocation(address=name, maps_url=url, source="url_paste")

    except Exception:
        pass

    return None


def _build_location(lat: float, lng: float, original_url: str) -> MapLocation:
    maps_url = f"https://www.google.com/maps?q={lat},{lng}"
    return MapLocation(lat=lat, lng=lng, maps_url=maps_url, source="url_paste")


async def resolve_short_url(url: str) -> str:
    """Follow redirects for known Google Maps short-URL hosts. Returns the final URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lstrip("www.")
    if host not in _ALLOWED_SHORT_HOSTS:
        return url

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=5.0,
            max_redirects=5,
        ) as client:
            try:
                resp = await client.head(url)
                final_url = str(resp.url)
            except httpx.HTTPStatusError:
                resp = await client.get(url)
                final_url = str(resp.url)

        if not _is_safe_url(final_url):
            return url
        return final_url
    except Exception:
        return url


async def resolve_to_location(url: str) -> MapLocation:
    """
    Fully resolve a Maps URL (including short URLs) to a MapLocation.
    Never raises — worst case returns MapLocation(address=url, source="url_paste").
    """
    try:
        resolved_url = await resolve_short_url(url)
        result = parse_maps_url(resolved_url)
        if result:
            if not result.maps_url:
                result.maps_url = resolved_url
            return result
    except Exception:
        pass

    return MapLocation(address=url, maps_url=url, source="url_paste")
