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
