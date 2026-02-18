"""
Request lifecycle middleware for YugenHub Backend.
Generates request IDs, populates context variables, and logs request/response details.
"""

import time
import uuid
import traceback
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from logging_config import get_logger, request_id_var, agency_id_var, user_id_var
from jose import jwt
from config import config

logger = get_logger("middleware")

SECRET_KEY = config.SECRET_KEY
ALGORITHM = config.ALGORITHM


def _extract_user_from_token(request: Request) -> tuple[str, str]:
    """Extract user_id and agency_id from the Authorization header JWT (best-effort)."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return "-", "-"
    
    token = auth_header[7:]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub", "-")
        agency_id = payload.get("agency_id", "-")
        return user_id, agency_id
    except Exception:
        return "-", "-"


class RequestLifecycleMiddleware(BaseHTTPMiddleware):
    """Middleware that adds request tracing and lifecycle logging."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate short request ID
        req_id = uuid.uuid4().hex[:8]
        request_id_var.set(req_id)

        # Extract user context from JWT (best-effort, won't fail)
        user_id, agency_id = _extract_user_from_token(request)
        user_id_var.set(user_id)
        agency_id_var.set(agency_id)

        # Start timer
        start_time = time.perf_counter()
        method = request.method
        path = request.url.path

        # Log request start
        logger.info(
            f"→ {method} {path}",
            extra={"data": {"query": str(request.query_params) if request.query_params else None}}
        )

        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 1)

            # Log request completion
            log_fn = logger.info if response.status_code < 400 else logger.warning
            log_fn(
                f"← {method} {path} {response.status_code} ({duration_ms}ms)",
                extra={"data": {"status": response.status_code, "duration_ms": duration_ms}}
            )

            # Add request ID to response headers for client-side debugging
            response.headers["X-Request-ID"] = req_id
            return response

        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 1)
            logger.error(
                f"✖ {method} {path} UNHANDLED ERROR ({duration_ms}ms): {exc}",
                exc_info=True,
                extra={"data": {"duration_ms": duration_ms, "error": str(exc)}}
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "request_id": req_id},
                headers={"X-Request-ID": req_id}
            )
