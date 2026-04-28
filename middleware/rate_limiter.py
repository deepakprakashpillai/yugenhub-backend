import time
import asyncio
from fastapi import Request, HTTPException
from typing import Dict, Tuple

class SimpleRateLimiter:
    """
    A simple in-memory Token Bucket / Sliding Window rate limiter.
    Limits requests per specific key (e.g. agency_id or IP).
    """
    def __init__(self, requests_per_minute: int = 10):
        self.requests_per_minute = requests_per_minute
        self.window_seconds = 60
        # Stores keys to a tuple of (count, window_start_time)
        self._store: Dict[str, Tuple[int, float]] = {}
        self._lock = asyncio.Lock()

    async def check_rate_limit(self, key: str):
        """
        Check if the key has exceeded the rate limit. 
        Raises 429 Too Many Requests if so.
        """
        now = time.time()
        
        async with self._lock:
            if key in self._store:
                count, window_start = self._store[key]
                if now - window_start > self.window_seconds:
                    # Window reset
                    self._store[key] = (1, now)
                else:
                    if count >= self.requests_per_minute:
                        raise HTTPException(
                            status_code=429,
                            detail="Too many requests. Please try again later."
                        )
                    # Increment counter
                    self._store[key] = (count + 1, window_start)
            else:
                self._store[key] = (1, now)

from config import config

# Global instances for different routes as needed
agent_rate_limiter = SimpleRateLimiter(requests_per_minute=config.AGENT_RATE_LIMIT)

async def check_agent_rate_limit(request: Request):
    """
    Dependency to check rate limit for the agent query route.
    Uses the agency_id from the query params, or client IP as fallback.
    """
    agency_id = request.query_params.get("agency_id")
    if agency_id:
        key = f"agent_limit_{agency_id}"
    else:
        client_ip = request.client.host if request.client else "unknown"
        key = f"agent_limit_{client_ip}"

    await agent_rate_limiter.check_rate_limit(key)


# ── Editor portal rate limiters ───────────────────────────────────────────────

# identify: per-IP, tight — protects against credential stuffing before token auth
editor_identify_limiter = SimpleRateLimiter(requests_per_minute=10)

# part-url: per-token, high — a 5 GB file needs ~50 presigned URLs, allow headroom for retries
editor_parts_limiter = SimpleRateLimiter(requests_per_minute=200)

# write ops (comment, upload init/complete/abort, version init/complete): per-token, moderate
editor_write_limiter = SimpleRateLimiter(requests_per_minute=30)

# read ops (get portal data, version download): per-token, relaxed
editor_read_limiter = SimpleRateLimiter(requests_per_minute=60)


async def check_editor_identify_limit(request: Request):
    """Per-IP limit for the identify endpoint."""
    ip = request.client.host if request.client else "unknown"
    await editor_identify_limiter.check_rate_limit(f"editor_identify_{ip}")


async def check_editor_parts_limit(request: Request):
    """Per-token high-volume limit for presigned part-URL requests."""
    token = request.path_params.get("token", "unknown")
    await editor_parts_limiter.check_rate_limit(f"editor_parts_{token}")


async def check_editor_write_limit(request: Request):
    """Per-token moderate limit for write operations (upload, comment)."""
    token = request.path_params.get("token", "unknown")
    await editor_write_limiter.check_rate_limit(f"editor_write_{token}")


async def check_editor_read_limit(request: Request):
    """Per-token relaxed limit for read operations."""
    token = request.path_params.get("token", "unknown")
    await editor_read_limiter.check_rate_limit(f"editor_read_{token}")
