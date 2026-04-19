"""
Tests for maps URL parsing utility and /api/maps/resolve endpoint.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from utils.maps import parse_maps_url, resolve_short_url, resolve_to_location


# ─── Unit: parse_maps_url ────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected_lat,expected_lng", [
    # @lat,lng,zoom format
    ("https://www.google.com/maps/@12.9716,77.5946,17z", 12.9716, 77.5946),
    ("https://www.google.com/maps/place/Bengaluru/@12.9716,77.5946,15z/data=...", 12.9716, 77.5946),
    # !3d!4d format
    ("https://www.google.com/maps/place/Gateway+of+India/@18.9219839,72.8346256,17z/data=!3m1!4b1!4m6!3m5!1s0x3be7d1dcd3c1757d:0x7f9ec7cd3c6b96e1!8m2!3d18.9219839!4d72.8346256", 18.9219839, 72.8346256),
    # ?q=lat,lng
    ("https://maps.google.com/?q=28.6139,77.2090", 28.6139, 77.2090),
    ("https://www.google.com/maps?q=19.0760,72.8777", 19.0760, 72.8777),
    # ?ll=lat,lng
    ("https://maps.google.com/maps?ll=13.0827,80.2707", 13.0827, 80.2707),
    # Negative coordinates
    ("https://www.google.com/maps/@-33.8688,151.2093,15z", -33.8688, 151.2093),
    ("https://www.google.com/maps?q=-33.8688,151.2093", -33.8688, 151.2093),
    # More decimal places
    ("https://www.google.com/maps/@48.858844,2.294351,17z", 48.858844, 2.294351),
])
def test_parse_maps_url_extracts_coords(url, expected_lat, expected_lng):
    result = parse_maps_url(url)
    assert result is not None, f"Expected coords from {url}"
    assert result.lat == pytest.approx(expected_lat, rel=1e-4)
    assert result.lng == pytest.approx(expected_lng, rel=1e-4)
    assert result.maps_url is not None


@pytest.mark.parametrize("url,expected_place_id", [
    ("https://maps.google.com/?q=place_id:ChIJdd4hrwug2EcRmSrV3Vo6llI", "ChIJdd4hrwug2EcRmSrV3Vo6llI"),
])
def test_parse_maps_url_extracts_place_id(url, expected_place_id):
    result = parse_maps_url(url)
    assert result is not None
    assert result.place_id == expected_place_id


def test_parse_maps_url_extracts_place_name():
    url = "https://www.google.com/maps/place/Taj+Mahal/"
    result = parse_maps_url(url)
    assert result is not None
    assert result.address is not None
    assert "Taj" in result.address or "Mahal" in result.address


@pytest.mark.parametrize("url", [
    "",
    None,
    "not a url",
    "https://www.example.com/",
    "https://www.google.com/",
])
def test_parse_maps_url_returns_none_for_non_maps(url):
    result = parse_maps_url(url)
    assert result is None


# ─── Unit: resolve_short_url allowlist ───────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_short_url_rejects_non_maps_host():
    """Non-Maps hosts must be returned unchanged — no HTTP call."""
    url = "https://evil.example.com/redirect"
    result = await resolve_short_url(url)
    assert result == url


@pytest.mark.asyncio
async def test_resolve_short_url_rejects_private_ip():
    """Even for an allowed host, redirect to a private IP must be blocked."""
    # Simulate a redirect to a private IP
    mock_response = MagicMock()
    mock_response.url = "http://192.168.1.1/sensitive"
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.head = AsyncMock(return_value=mock_response)

    with patch("utils.maps.httpx.AsyncClient", return_value=mock_client):
        result = await resolve_short_url("https://maps.app.goo.gl/abc")
    # Should return original URL, not the private-IP redirect
    assert result == "https://maps.app.goo.gl/abc"


@pytest.mark.asyncio
async def test_resolve_short_url_follows_valid_redirect():
    """A short URL that redirects to a legitimate Maps URL should be followed."""
    final_url = "https://www.google.com/maps/@12.9716,77.5946,17z"
    mock_response = MagicMock()
    mock_response.url = final_url
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.head = AsyncMock(return_value=mock_response)

    with patch("utils.maps.httpx.AsyncClient", return_value=mock_client):
        result = await resolve_short_url("https://maps.app.goo.gl/abc")
    assert result == final_url


# ─── Unit: resolve_to_location never raises ──────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_to_location_returns_fallback_on_garbage():
    result = await resolve_to_location("garbage not a url")
    assert result is not None
    assert result.source == "url_paste"


@pytest.mark.asyncio
async def test_resolve_to_location_parses_known_url():
    url = "https://www.google.com/maps/@19.0760,72.8777,15z"
    result = await resolve_to_location(url)
    assert result.lat == pytest.approx(19.0760, rel=1e-4)
    assert result.lng == pytest.approx(72.8777, rel=1e-4)


# ─── Integration: POST /api/maps/resolve endpoint ────────────────────────────

@pytest.mark.asyncio
async def test_resolve_endpoint_requires_auth(async_client):
    resp = await async_client.post("/api/maps/resolve", json={"url": "https://maps.google.com/?q=0,0"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_resolve_endpoint_rejects_missing_url(async_client, test_user):
    from routes.deps import create_access_token
    token = create_access_token({"sub": test_user["id"]})
    resp = await async_client.post(
        "/api/maps/resolve",
        json={},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_resolve_endpoint_returns_map_location_for_valid_url(async_client, test_user):
    from routes.deps import create_access_token
    token = create_access_token({"sub": test_user["id"]})
    url = "https://www.google.com/maps/@19.0760,72.8777,15z"
    resp = await async_client.post(
        "/api/maps/resolve",
        json={"url": url},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["lat"] == pytest.approx(19.0760, rel=1e-4)
    assert data["lng"] == pytest.approx(72.8777, rel=1e-4)
    assert data["source"] == "url_paste"


@pytest.mark.asyncio
async def test_resolve_endpoint_returns_fallback_for_unknown_url(async_client, test_user):
    """Malformed/unrecognized URL → 200 with address fallback, not 500."""
    from routes.deps import create_access_token
    token = create_access_token({"sub": test_user["id"]})
    resp = await async_client.post(
        "/api/maps/resolve",
        json={"url": "https://not-a-maps-url.com/foo"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "url_paste"


@pytest.mark.asyncio
async def test_resolve_endpoint_host_allowlist(async_client, test_user):
    """Non-Maps short-URL hosts are returned as-is without following redirects."""
    from routes.deps import create_access_token
    token = create_access_token({"sub": test_user["id"]})
    url = "https://bit.ly/notamapslink"
    resp = await async_client.post(
        "/api/maps/resolve",
        json={"url": url},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    # Should not raise; result is a fallback MapLocation
    data = resp.json()
    assert isinstance(data, dict)
